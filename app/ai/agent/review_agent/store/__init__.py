# -*- coding: utf-8 -*-
"""评审会记录存取层

见 question_store.py：图的状态里只留索引，问答正文放在 MySQL 的 review_question 表里，
谁要用正文谁拿 id 去取。
"""
