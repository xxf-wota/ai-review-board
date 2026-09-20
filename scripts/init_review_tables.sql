-- AI 交叉质询评审团：评审会数据表
-- 与 exam_agent 原有的 question_bank / user_info 同库（agent_project）
-- 全部 IF NOT EXISTS，可重复执行

-- 一次评审会
CREATE TABLE IF NOT EXISTS review_session (
  session_id    VARCHAR(64)  NOT NULL COMMENT '会话ID，前后端共用的会议标识',
  plan_title    VARCHAR(255) NOT NULL DEFAULT '' COMMENT '方案标题',
  plan_text     LONGTEXT     COMMENT '学生提交的方案全文',
  plan_elements TEXT         COMMENT '方案要素表，JSON 字符串',
  student_id    VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '提交方案的学生',
  status        VARCHAR(32)  NOT NULL DEFAULT 'submitted' COMMENT 'submitted 已提交 / running 进行中 / finished 已结束',
  round         INT          NOT NULL DEFAULT 0 COMMENT '当前进行到第几轮',
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (session_id),
  KEY idx_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='评审会';

-- 质询记录（核心表）
-- speaker_role + question_type + target_speaker 三个字段就能还原出"谁问了谁、是主问题还是接话"
CREATE TABLE IF NOT EXISTS review_question (
  id             INT          NOT NULL AUTO_INCREMENT,
  session_id     VARCHAR(64)  NOT NULL,
  round          INT          NOT NULL DEFAULT 1 COMMENT '第几轮评审',
  speaker_role   VARCHAR(32)  NOT NULL COMMENT 'tech / cost / compliance / user',
  question_type  VARCHAR(32)  NOT NULL DEFAULT 'main' COMMENT 'main 主问题 / cross 交叉质询 / followup 追问',
  target_speaker VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '交叉质询时被反驳的那位评审',
  question       TEXT         NOT NULL COMMENT '评审提出的问题',
  student_answer TEXT         COMMENT '学生的回答',
  verdict        VARCHAR(32)  NOT NULL DEFAULT '' COMMENT 'resolved 答好了 / partial 答偏了 / unresolved 没答',
  severity       INT          NOT NULL DEFAULT 0 COMMENT '严重度 1~5',
  followup_depth INT          NOT NULL DEFAULT 0 COMMENT '追问层数',
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_session_round (session_id, round)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='质询记录';

-- 能力诊断
CREATE TABLE IF NOT EXISTS review_score (
  session_id   VARCHAR(64) NOT NULL,
  feasibility  INT         NOT NULL DEFAULT 0 COMMENT '可行性',
  completeness INT         NOT NULL DEFAULT 0 COMMENT '完备性',
  rigor        INT         NOT NULL DEFAULT 0 COMMENT '严谨性',
  expression   INT         NOT NULL DEFAULT 0 COMMENT '表达',
  summary      TEXT        COMMENT '一句话总评',
  created_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='能力诊断';
