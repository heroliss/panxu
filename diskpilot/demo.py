"""Synthetic examples. No example path is ever read or changed."""
import hashlib


def demo_items():
    rows = [
        ('cache-folder', 'directory', '图形着色器缓存', r'C:\演练\缓存\ShaderCache', '图形驱动', '可再生成缓存', 73400320, 'low', 'delete', '已知缓存配方；下次使用图形应用时可能重建。'),
        ('cache-file', 'file', '着色器缓存示例.bin', r'C:\演练\缓存\ShaderCache\着色器缓存示例.bin', '图形驱动', '可再生成缓存', 73400320, 'low', 'delete', '演练配方确认可再生成。'),
        ('installer', 'file', '示例工具-旧安装包.exe', r'D:\演练下载\示例工具-旧安装包.exe', '下载资料', '安装包', 230686720, 'review', 'delete', '安装包不等于已安装程序，需要确认离线留存需求。'),
        ('temp', 'directory', '已结束测试的临时目录', r'D:\演练\测试临时目录', '测试任务', '临时工作资料', 18874368, 'review', 'delete', '仅演练数据；真实 Temp 目录不能仅按名称认定无用。'),
        ('personal', 'file', '个人作品.psd', r'E:\演练作品\个人作品.psd', '个人资料', '个人作品', 134217728, 'protected', 'delete', '用户保留规则：个人作品。'),
        ('project', 'directory', 'Unity 项目源码与素材', r'D:\演练项目\Assets', 'Unity 工程', '项目资料', 314572800, 'protected', 'delete', '源码和素材必须保持完整，不能作为缓存处理。'),
        ('system', 'file', 'pagefile.sys', r'C:\pagefile.sys', 'Windows', '系统组件', 4294967296, 'protected', 'delete', '分页文件由 Windows 管理。'),
        ('privacy', 'file', '浏览器登录与站点数据', r'C:\演练\BrowserProfile\Cookies', '浏览器', '隐私与账号', 2097152, 'protected', 'delete', '登录状态、站点存储不属于普通缓存；首版不操作敏感数据库。'),
        ('startup', 'startup', '示例跨设备工具', '演练启动项 / CrossDevice', '示例软件', '开机启动', 0, 'review', 'disable_startup', '模拟禁用只改变本工具内的状态；实际功能可能不能开机就绪。'),
        ('startup-system', 'startup', 'Windows 安全通知', '演练启动项 / SecurityHealth', 'Microsoft', '开机启动', 0, 'protected', 'disable_startup', '系统安全相关项保留。'),
        ('service', 'service', '示例下载后台服务', '演练服务 / DownloadHelper', '示例下载工具', '后台服务', 0, 'review', 'stop_service', '可能影响下载支持；本版只模拟停止，不更改真实服务。'),
        ('service-system', 'service', 'Windows 安全核心服务', '演练服务 / WinDefend', 'Microsoft', '后台服务', 0, 'protected', 'stop_service', '系统关键服务保留。'),
        ('process', 'process', '示例协作工具.exe', '演练进程 / PID 424242', '示例协作工具', '常驻进程', 0, 'review', 'stop_process', '停止程序可能丢失未保存工作；模拟内存量不是实际释放量。'),
        ('software', 'software', '示例旧版编辑器', '演练软件 / ExampleEditor', '示例厂商', '已安装软件', 0, 'review', 'uninstall', '正式卸载需调用正规卸载器；此版只记录模拟卸载。'),
    ]
    result = []
    for key, kind, name, path, owner, category, size, risk, action, reason in rows:
        details = {'synthetic': True, 'complete': True, 'subtree_complete': True,
                   'can_simulate_delete': risk != 'protected',
                   'verified_cache': key == 'cache-file', 'observed_at': 'synthetic'}
        if kind == 'process':
            details.update(pid=424242, memory_bytes=209715200, state='正在运行（演练）')
        if kind == 'service':
            details.update(state='Running（演练）', start_mode='Auto（演练）')
        if kind == 'startup':
            details['state'] = '启用（演练）'
        if kind == 'software':
            details.update(version='1.0（演练）', estimated_install_bytes=536870912)
        result.append(dict(id='demo-' + key, kind=kind, name=name, path=path, owner=owner,
                           category=category, size_bytes=size, allocated_bytes=None, risk=risk,
                           recommendation='保留' if risk == 'protected' else ('可模拟清理' if risk == 'low' else '确认用途后处理'),
                           reason=reason, impact=reason, actions=[] if risk == 'protected' else [action],
                           signature=hashlib.sha256(key.encode()).hexdigest(), details=details,
                           evidence=['内置演练样本；不对应真实文件、程序或设置'], simulated=False))
    return result
