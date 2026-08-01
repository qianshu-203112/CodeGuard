"""python -m code_guard.viz 入口"""
from code_guard.viz import generate_html
import sys

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CodeGuard 可视化")
    parser.add_argument("path", help="项目路径")
    parser.add_argument("-o", "--output", default="code_graph.html", help="输出 HTML 路径")
    args = parser.parse_args()
    generate_html(args.path, args.output)
