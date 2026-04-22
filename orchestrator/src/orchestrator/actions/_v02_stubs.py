"""Placeholder（v0.2-S4 之前的 stub，已被真实现替代）。

保留文件是为了防止误 import；真正的 handler 已迁到：
- create_staging_test.py
- create_pr_ci_watch.py
- teardown_accept_env.py

这三个模块会在 actions/__init__.py 的 from-import 时触发 @register。
本文件不做 registration，保持空壳以兼容旧 snapshot。
"""
from __future__ import annotations
