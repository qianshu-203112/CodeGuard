#!/usr/bin/env python
"""CodeGuard CLI 快捷入口"""
import sys
import os

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from code_guard.cli.main import main

if __name__ == "__main__":
    main()
