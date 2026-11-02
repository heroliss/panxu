"""Bounded current-user UserAssist evidence, never complete app-use telemetry.

Format: Plaso userassist.py/userassist.yaml (version 5, exactly 72 bytes).
Names and executable paths are used locally for exact references only. The
result contains neither paths, commands, raw registry values nor app lists.
"""
from collections import Counter, defaultdict
import codecs
from datetime import datetime, timedelta, timezone
import hashlib
import ntpath
import os
import re
import struct
import time


USERASSIST_KEY = r'Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist'
TRACKING_KEY = r'Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced'
MAX_GROUPS, MAX_VALUES, MAX_BYTES, MAX_SECONDS = 16, 2048, 1024 * 1024, 1.0
_WRAPPERS = {'cmd.exe', 'powershell.exe', 'pwsh.exe', 'wscript.exe', 'cscript.exe', 'rundll32.exe',
             'regsvr32.exe', 'msiexec.exe', 'explorer.exe', 'svchost.exe', 'dllhost.exe',
             'setup.exe', 'update.exe', 'uninstall.exe', 'uninstaller.exe', 'launcher.exe',
             'bootstrapper.exe', 'maintenancetool.exe'}
MATCH_TYPES = frozenset(('process_executable_reference', 'startup_executable_reference',
                         'registered_executable_reference'))
REASONS = frozenset(('no_exact_executable', 'source_unavailable', 'source_not_supported', 'source_missing',
    'no_matching_record', 'source_scan_limited', 'unsupported_format', 'invalid_record',
    'multiple_counters', 'shared_registration_reference', 'not_a_gui_usage_source', 'timestamp_unknown',
    'source_tracking_disabled'))


def executable_digest(path, *, environment=None):
    """Match a local absolute EXE reference; no basename or fuzzy-name match."""
    if not isinstance(path, str) or not path or len(path) > 1024 or any(ord(c) < 32 for c in path):
        return None
    if environment is None:
        path = ntpath.expandvars(path)
    else:
        lowered = {key.casefold(): value for key, value in environment.items()}
        path = re.sub(r'%([^%]+)%', lambda match: lowered.get(match[1].casefold(), match[0]), path)
    path = path.replace('/', '\\')
    if (not re.match(r'^[A-Za-z]:\\', path) or ':' in path[2:] or '%' in path or any(c in path for c in '*?"<>|')
            or any(part in ('.', '..') for part in path.split('\\')) or not path.casefold().endswith('.exe')):
        return None
    basename = ntpath.basename(path).casefold()
    if basename in _WRAPPERS or re.fullmatch(r'unins\d*\.exe', basename):
        return None
    normalized = ntpath.normcase(ntpath.normpath(path))
    return hashlib.sha256(normalized.encode('utf-8', errors='surrogatepass')).hexdigest()


def command_executable_digest(command):
    """Only unambiguous executable prefixes; never execute/resolve a command."""
    if not isinstance(command, str) or len(command) > 32767:
        return None
    quoted = re.match(r'^\s*"([^"\r\n]+\.exe)"(?:\s|$)', command, re.IGNORECASE)
    plain = re.match(r'^\s*([^\s"\r\n]+\.exe)(?:\s|$)', command, re.IGNORECASE)
    match = quoted or plain
    return executable_digest(match[1]) if match else None


def registered_executable_digest(display_icon):
    """An explicit registration EXE reference, not proof of the app's main EXE."""
    if not isinstance(display_icon, str) or len(display_icon) > 2048:
        return None
    value = display_icon.strip()
    match = re.fullmatch(r'(?:(?:"([^"\r\n]+\.exe)")|([^"\r\n]+\.exe))(?:\s*,\s*-?\d+)?', value, re.IGNORECASE)
    return executable_digest(match[1] or match[2]) if match else None


def _record(data, observed):
    if not isinstance(data, bytes) or len(data) != 72:
        return None
    count, = struct.unpack_from('<I', data, 4)
    ticks, = struct.unpack_from('<Q', data, 60)
    last = None
    if ticks:
        try:
            stamp = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ticks // 10)
            if 1970 <= stamp.year and stamp <= observed + timedelta(days=1):
                last = stamp.isoformat()
        except (OverflowError, ValueError):
            pass
    return dict(last_recorded_launch_at=last, cumulative_launch_count=count)


class NativeUserAssistReader:
    """Only names/type/size are enumerated; matched values get a 72-byte read."""
    def __init__(self):
        self.status, self.partial = 'unavailable', False

    def entries(self, max_groups):
        if os.name != 'nt':
            self.status = 'not_supported'
            return
        import ctypes
        from ctypes import wintypes
        import winreg
        advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        enum = advapi.RegEnumValueW
        enum.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
                        wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD))
        enum.restype = wintypes.LONG
        query = advapi.RegQueryValueExW
        query.argtypes = (wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPVOID,
                         ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD))
        query.restype = wintypes.LONG
        # An explicit opt-out makes any leftover counters historical/stale.
        # Read only a bounded DWORD, and never enable tracking ourselves.
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TRACKING_KEY, 0, winreg.KEY_READ) as tracking:
                flag, kind, size = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD(4)
                status = query(int(tracking), 'Start_TrackProgs', None, ctypes.byref(kind), ctypes.byref(flag), ctypes.byref(size))
                if status == 0 and kind.value == winreg.REG_DWORD and size.value == 4 and flag.value == 0:
                    self.status = 'tracking_disabled'
                    return
        except OSError:
            pass
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, USERASSIST_KEY, 0, winreg.KEY_READ) as root:
                self.status = 'available'
                group_count = winreg.QueryInfoKey(root)[0]
                self.partial = group_count > max_groups
                for group_index in range(min(group_count, max_groups)):
                    group = winreg.EnumKey(root, group_index)
                    try:
                        with winreg.OpenKey(root, group, 0, winreg.KEY_READ) as sub:
                            version, kind, size = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD(4)
                            status = query(int(sub), 'Version', None, ctypes.byref(kind), ctypes.byref(version), ctypes.byref(size))
                            if status or kind.value != winreg.REG_DWORD or size.value != 4 or version.value != 5:
                                self.partial = True
                                yield dict(version=None, name='', size=0, type=None, read=None, group=group)
                                continue
                            with winreg.OpenKey(sub, 'Count', 0, winreg.KEY_READ) as count:
                                index = 0
                                while True:
                                    name = ctypes.create_unicode_buffer(1025)
                                    length, data_type, size = wintypes.DWORD(1025), wintypes.DWORD(), wintypes.DWORD()
                                    status = enum(int(count), index, name, ctypes.byref(length), None, ctypes.byref(data_type), None, ctypes.byref(size))
                                    if status == 259:
                                        break
                                    index += 1
                                    if status != 0:
                                        self.partial = True
                                        yield dict(version=5, name='', size=0, type=None, read=None, group=group)
                                        if status != 234:
                                            break
                                        continue

                                    def read(handle=count, value_name=name.value):
                                        buffer = ctypes.create_string_buffer(72)
                                        data_length, value_type = wintypes.DWORD(72), wintypes.DWORD()
                                        result = query(int(handle), value_name, None, ctypes.byref(value_type), buffer, ctypes.byref(data_length))
                                        if result or value_type.value != winreg.REG_BINARY or data_length.value != 72:
                                            raise OSError('userassist_record_changed')
                                        return bytes(buffer.raw)

                                    yield dict(version=5, name=name.value, size=size.value, type=data_type.value, read=read, group=group)
                    except OSError:
                        self.partial = True
        except FileNotFoundError:
            self.status = 'missing'
        except OSError:
            self.status = 'unavailable'


def probe_userassist_source():
    """Reachability only: never enumerate application names or history values."""
    status = 'not_supported'
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, USERASSIST_KEY, 0, winreg.KEY_READ):
                status = 'available'
        except FileNotFoundError:
            status = 'missing'
        except OSError:
            status = 'unavailable'
    return dict(source='windows_userassist', status=status, read_only=True, administrator_required=False)


def enrich_usage_evidence(items, *, reader=None, observed=None, clock=None,
                          max_values=MAX_VALUES, max_bytes=MAX_BYTES, max_seconds=MAX_SECONDS):
    """One bounded acquisition per inventory; list/inspect only use its snapshot."""
    observed = observed or datetime.now(timezone.utc)
    clock = clock or time.monotonic
    deadline = clock() + max_seconds
    bindings, shared_software = {}, Counter()
    for item in items:
        if item.get('kind') not in ('process', 'startup', 'software'):
            continue
        details = item.get('details', {})
        key, match_type = details.get('_usage_executable_digest'), details.get('_usage_match_type')
        if isinstance(key, str) and re.fullmatch(r'[a-f0-9]{64}', key) and match_type in MATCH_TYPES:
            bindings[item['id']] = (key, match_type)
            if item['kind'] == 'software':
                shared_software[key] += 1
    targets = {key for key, _ in bindings.values()}
    reader = reader or NativeUserAssistReader()
    found, seen = defaultdict(list), set()
    scanned = consumed = invalid = unsupported = unresolved = 0
    limited = False
    if targets:
        entries = reader.entries(MAX_GROUPS)
        try:
            while True:
                if clock() >= deadline or scanned >= max_values:
                    limited = True
                    break
                try:
                    entry = next(entries)
                except StopIteration:
                    break
                scanned += 1
                name = entry.get('name')
                if not isinstance(name, str) or len(name) > 1024:
                    invalid += 1
                    continue
                consumed += len(name.encode('utf-16-le', errors='surrogatepass'))
                if consumed > max_bytes:
                    limited = True
                    break
                if entry.get('version') != 5:
                    unsupported += 1
                    continue
                decoded = codecs.decode(name, 'rot_13')
                if decoded.startswith('UEME_RUNPATH:'):
                    decoded = decoded[len('UEME_RUNPATH:'):]
                key = executable_digest(decoded)
                if key is None:
                    # Known-folder GUIDs, shortcuts and package/AUMID names
                    # are not resolved. Do not claim complete negative coverage
                    # just because those references could not be matched.
                    if decoded and (not decoded.startswith('UEME_') or decoded.startswith(('UEME_RUNPIDL:', 'UEME_RUNCPL:'))):
                        unresolved += 1
                    continue
                if key not in targets:
                    continue
                ref = (entry.get('group'), name.casefold())
                if ref in seen:
                    continue
                seen.add(ref)
                if entry.get('type') != 3 or entry.get('size') != 72 or not callable(entry.get('read')):
                    invalid += 1
                    continue
                if consumed + 72 > max_bytes:
                    limited = True
                    break
                try:
                    data = entry['read']()
                    consumed += len(data) if isinstance(data, bytes) else 0
                    record = _record(data, observed)
                    if record is None:
                        invalid += 1
                    else:
                        found[key].append(record)
                except OSError:
                    invalid += 1
        finally:
            close = getattr(entries, 'close', None)
            if callable(close):
                close()
    partial = limited or bool(getattr(reader, 'partial', False)) or bool(invalid or unsupported or unresolved)
    source_status = getattr(reader, 'status', 'unavailable') if targets else 'not_queried'
    for item in items:
        if item.get('kind') not in ('process', 'startup', 'software', 'service'):
            continue
        local = dict(source='windows_userassist', coverage='current_user_shell_launches', status='unknown',
            match_type=None, last_recorded_launch_at=None, cumulative_launch_count=None,
            window_start_at=None, window_end_at=None, frequency_per_30_days=None,
            observed_at=observed.isoformat(), counter_reset_possible=True, source_status=source_status,
            source_partial=partial, matched_record_count=0)
        binding = bindings.get(item['id'])
        if item['kind'] == 'service':
            local['reason'] = 'not_a_gui_usage_source'
        elif not binding:
            local['reason'] = 'no_exact_executable'
        elif item['kind'] == 'software' and shared_software[binding[0]] > 1:
            local['reason'] = 'shared_registration_reference'
        elif source_status != 'available':
            local['reason'] = {'missing': 'source_missing', 'not_supported': 'source_not_supported',
                               'tracking_disabled': 'source_tracking_disabled'}.get(source_status, 'source_unavailable')
        elif not found.get(binding[0]):
            local['reason'] = ('source_scan_limited' if partial else 'no_matching_record')
        else:
            records = found[binding[0]]
            dates = [record['last_recorded_launch_at'] for record in records if record['last_recorded_launch_at']]
            local.update(status='partial' if partial or len(records) > 1 else 'matched',
                match_type=binding[1], matched_record_count=len(records),
                last_recorded_launch_at=max(dates) if dates else None,
                cumulative_launch_count=records[0]['cumulative_launch_count'] if len(records) == 1 else None)
            if len(records) > 1:
                local['reason'] = 'multiple_counters'
            elif partial:
                local['reason'] = 'source_scan_limited'
            elif not dates:
                local['reason'] = 'timestamp_unknown'
        item.setdefault('details', {})['usage_evidence'] = local
    return dict(source='windows_userassist', source_status=source_status, partial=partial,
                values_checked=scanned, bytes_read=consumed, invalid_records=invalid,
                unsupported_formats=unsupported, unsupported_references=unresolved, limit_reached=limited)
