# -*- coding: utf-8 -*-
"""Unit tests for the Cloud Test Runtime layer (Commit 05).

Isolation rules: all environment control goes through pytest monkeypatch.
No real network access, no nftables changes, no system env mutation.
"""
