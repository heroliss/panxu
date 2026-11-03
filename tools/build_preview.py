"""Write a self-contained UI preview (fake backend) for checking layouts in a browser.

    python tools/build_preview.py [output.html] [--page junk] [--theme dark]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panxu import ui_bundle  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output', nargs='?', default='preview.html')
    parser.add_argument('--page', default='', help='open this page after start (e.g. junk, space)')
    parser.add_argument('--theme', default='', help='force light or dark')
    parser.add_argument('--script', default='', help='extra JS to run after start')
    parser.add_argument('--wide', type=float, default=0, help='simulate a window this many px wide in a narrow panel')
    args = parser.parse_args()
    html = ui_bundle.build(extra_scripts=['dev/mock.js'])
    boot = []
    if args.theme:
        boot.append(f"PX.state.init.settings.theme='{args.theme}';PX.applyTheme('{args.theme}');")
    if args.page:
        boot.append(f"PX.go('{args.page}');")
    if args.script:
        boot.append(args.script)
    if args.wide:
        z = 757 / args.wide
        html = html.replace('</head>', '<style>html{zoom:%.4f}.shell{height:calc(100vh / %.4f)}'
                            '@media (max-width:760px){.shell{grid-template-columns:212px 1fr}'
                            '.nav-item span,.brand span,.nav-badge,.rail-foot{display:revert}'
                            '.brand b{font-size:19px;letter-spacing:.12em}.page{padding:26px 36px 96px}}'
                            '@media (max-width:1100px){.space-grid{grid-template-columns:minmax(0,1.25fr) minmax(300px,1fr)}'
                            '.treemap-wrap{position:sticky;max-width:none}}</style></head>' % (z, z))
    if boot:
        snippet = ('<script>document.addEventListener("DOMContentLoaded",()=>{const t=setInterval(()=>{'
                   'if(PX.state.init){clearInterval(t);' + ''.join(boot) + '}},30);});</script>')
        html = html.replace('</body>', snippet + '\n</body>')
    with open(args.output, 'w', encoding='utf-8') as fh:
        fh.write(html)
    print(args.output)


if __name__ == '__main__':
    main()
