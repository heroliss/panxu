import os
import struct
import unittest
import zlib

from helpers import TempDirTest

from panxu import icons, winapi


class PngTest(unittest.TestCase):
    def test_encode_png(self):
        rgba = bytes([255, 0, 0, 255, 0, 255, 0, 128, 0, 0, 255, 0, 9, 9, 9, 9])
        png = icons.encode_png(2, 2, rgba)
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        width, height, depth, colour = struct.unpack('>IIBB', png[16:26])
        self.assertEqual((width, height, depth, colour), (2, 2, 8, 6))
        idat = png.index(b'IDAT')
        length = struct.unpack('>I', png[idat - 4:idat])[0]
        raw = zlib.decompress(png[idat + 4:idat + 4 + length])
        self.assertEqual(raw, b'\x00' + rgba[:8] + b'\x00' + rgba[8:])
        self.assertTrue(icons.data_url(png).startswith('data:image/png;base64,'))

    def test_unblend_recovers_alpha(self):
        # BGRA as GDI writes it: an opaque red, a transparent pixel and a half-transparent blue
        on_black = bytes([0, 0, 255, 0, 0, 0, 0, 0, 128, 0, 0, 0])
        on_white = bytes([0, 0, 255, 255, 255, 255, 255, 255, 255, 127, 127, 255])
        out = icons.unblend(on_black, on_white)
        self.assertEqual(tuple(out[0:4]), (255, 0, 0, 255))
        self.assertEqual(out[7], 0)
        self.assertEqual(out[11], 128)
        self.assertEqual(out[10], 255, 'colour scaled back up by alpha')


class ExtractTest(TempDirTest):
    @unittest.skipUnless(winapi.IS_WINDOWS, 'icons are read from Windows executables')
    def test_icon_of_a_windows_program(self):
        explorer = os.path.join(os.environ.get('SystemRoot', 'C:\\Windows'), 'explorer.exe')
        png = icons.file_icon(explorer)
        self.assertIsNotNone(png)
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')

    def test_no_icon(self):
        self.assertIsNone(icons.file_icon(self.make('plain.txt', 10)))
        self.assertIsNone(icons.file_icon(os.path.join(self.tmp, 'missing.exe')))
        self.assertIsNone(icons.file_icon(''))


if __name__ == '__main__':
    unittest.main()
