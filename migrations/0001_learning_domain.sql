-- 0001_learning_domain.sql —— 学习领域的 PostgreSQL schema（ADR-0026）
--
-- 三条设计原则：
--
-- 1. **身份与外键用列，内层形状用 JSONB。** 领域模型是不可变的聚合，整体替换
--    （ADR-0015）。因此一个聚合 = 一行 = 一份 JSONB 负载；而**跨聚合的引用**
--    （目标属于谁、诊断针对哪个目标、进度属于哪门课）用**普通列**表达，好建外键、
--    好建索引。负载里也有这些值——用 CHECK 约束把两者**绑死**，不让它们各说一套。
--
-- 2. **只存历史，不存派生状态。** 目标状态由迁移历史派生、知识点学习状态由迁移
--    历史派生（ADR-0017、ADR-0018）。存派生值就要同步两处，而两处会漂移。
--
-- 3. **负载必须带 schema_version。** 领域形状已经变过两次（ADR-0017 去掉
--    GoalConstraint 与 status 字段，ADR-0023 去掉 correct 与 basis）。约束只要求
--    它**存在且是数字**，不钉死具体值——版本升级不需要改 DDL，但**没有版本的负载
--    进不来**。
--
-- 乐观锁：每张聚合表都有 revision 列。写入时携带的版本必须等于库里的当前版本，
-- 否则冲突（ADR-0026）。DDL 只保证 revision >= 0，比较规则在应用层。
--
-- **一个必须知道的 PostgreSQL 行为**：CHECK 约束在表达式为 NULL 时**放行**（NULL
-- 不是 FALSE）。而 `payload ->> 'id'` 在键缺失时返回 NULL——直接写 CHECK 会让
-- 「负载缺字段」的行**全部漏过去**。因此下面每一条 payload 检查都用 COALESCE
-- 把「键缺失」变成一个确定的假值。这一条是实测出来的，不是推测。

BEGIN;

-- ---------------------------------------------------------------------------
-- 学习者与课程
-- ---------------------------------------------------------------------------

CREATE TABLE learners (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    background  TEXT[] NOT NULL DEFAULT '{}',
    preferences TEXT[] NOT NULL DEFAULT '{}',
    revision    INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT learners_revision_non_negative CHECK (revision >= 0)
);

CREATE TABLE courses (
    id       TEXT PRIMARY KEY,
    title    TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT courses_revision_non_negative CHECK (revision >= 0)
);

-- 知识点是**课程内**的身份（ADR-0016）：主键是 (course_id, id)。
CREATE TABLE knowledge_points (
    course_id TEXT NOT NULL REFERENCES courses (id) ON DELETE CASCADE,
    id        TEXT NOT NULL,
    title     TEXT NOT NULL,
    summary   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (course_id, id)
);

CREATE TABLE knowledge_relations (
    course_id TEXT NOT NULL REFERENCES courses (id) ON DELETE CASCADE,
    kind      TEXT NOT NULL,
    source    TEXT NOT NULL,
    target    TEXT NOT NULL,
    PRIMARY KEY (course_id, kind, source, target),
    CONSTRAINT knowledge_relations_kind CHECK (kind IN ('requires', 'part_of')),
    CONSTRAINT knowledge_relations_no_self_loop CHECK (source <> target),
    CONSTRAINT knowledge_relations_source_fk
        FOREIGN KEY (course_id, source) REFERENCES knowledge_points (course_id, id),
    CONSTRAINT knowledge_relations_target_fk
        FOREIGN KEY (course_id, target) REFERENCES knowledge_points (course_id, id)
);

-- ---------------------------------------------------------------------------
-- 学习目标
-- ---------------------------------------------------------------------------

CREATE TABLE learning_goals (
    id         TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL REFERENCES learners (id),
    course_id  TEXT NOT NULL REFERENCES courses (id),
    payload    JSONB NOT NULL,
    revision   INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT learning_goals_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT learning_goals_payload_version
        CHECK (COALESCE(jsonb_typeof(payload -> 'schema_version'), 'missing') = 'number'),
    CONSTRAINT learning_goals_payload_id CHECK (COALESCE(payload ->> 'id', '') = id),
    CONSTRAINT learning_goals_payload_learner CHECK (COALESCE(payload ->> 'learner_id', '') = learner_id),
    CONSTRAINT learning_goals_payload_course CHECK (COALESCE(payload ->> 'course_id', '') = course_id),
    CONSTRAINT learning_goals_revision_non_negative CHECK (revision >= 0)
);

CREATE INDEX learning_goals_learner_idx ON learning_goals (learner_id);

-- ---------------------------------------------------------------------------
-- 学习进度
--
-- 身份是 (learner_id, course_id) 这一对（ADR-0018）。**整块存一份 JSONB**：
-- 领域层的更新粒度就是整体替换，存储粒度与之一致才不需要引入 diff 机制；
-- 一次读写 = 一行，原子性天然成立（不会读到「半个进度」）。代价是每次迁移重写
-- 整块（O(知识点数)）——见 ADR-0026 的成本实测。
-- ---------------------------------------------------------------------------

CREATE TABLE learning_progress (
    learner_id TEXT NOT NULL REFERENCES learners (id),
    course_id  TEXT NOT NULL REFERENCES courses (id),
    payload    JSONB NOT NULL,
    revision   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (learner_id, course_id),
    CONSTRAINT learning_progress_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT learning_progress_payload_version
        CHECK (COALESCE(jsonb_typeof(payload -> 'schema_version'), 'missing') = 'number'),
    CONSTRAINT learning_progress_payload_learner CHECK (COALESCE(payload ->> 'learner_id', '') = learner_id),
    CONSTRAINT learning_progress_payload_course CHECK (COALESCE(payload ->> 'course_id', '') = course_id),
    CONSTRAINT learning_progress_revision_non_negative CHECK (revision >= 0)
);

-- ---------------------------------------------------------------------------
-- 诊断
-- ---------------------------------------------------------------------------

CREATE TABLE diagnoses (
    id         TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL REFERENCES learners (id),
    goal_id    TEXT NOT NULL REFERENCES learning_goals (id),
    payload    JSONB NOT NULL,
    revision   INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT diagnoses_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT diagnoses_payload_version
        CHECK (COALESCE(jsonb_typeof(payload -> 'schema_version'), 'missing') = 'number'),
    CONSTRAINT diagnoses_payload_id CHECK (COALESCE(payload ->> 'id', '') = id),
    CONSTRAINT diagnoses_payload_learner CHECK (COALESCE(payload ->> 'learner_id', '') = learner_id),
    CONSTRAINT diagnoses_payload_goal CHECK (COALESCE(payload ->> 'goal_id', '') = goal_id),
    CONSTRAINT diagnoses_revision_non_negative CHECK (revision >= 0)
);

CREATE INDEX diagnoses_goal_idx ON diagnoses (goal_id);

-- ---------------------------------------------------------------------------
-- 计划版本
--
-- 版本是**只增不改**的：修订产生新版本，旧版本永远保留（ADR-0025）。因此主键是
-- (plan_id, number)——同一个版本号写两次就是冲突。
-- ---------------------------------------------------------------------------

CREATE TABLE plan_versions (
    plan_id  TEXT NOT NULL,
    number   INTEGER NOT NULL,
    goal_id  TEXT NOT NULL REFERENCES learning_goals (id),
    payload  JSONB NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (plan_id, number),
    CONSTRAINT plan_versions_number_positive CHECK (number >= 1),
    CONSTRAINT plan_versions_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT plan_versions_payload_version
        CHECK (COALESCE(jsonb_typeof(payload -> 'schema_version'), 'missing') = 'number'),
    CONSTRAINT plan_versions_payload_plan CHECK (COALESCE(payload ->> 'plan_id', '') = plan_id),
    CONSTRAINT plan_versions_payload_number CHECK (COALESCE((payload ->> 'number')::int, -1) = number),
    CONSTRAINT plan_versions_payload_goal
        CHECK (COALESCE(payload -> 'plan' ->> 'goal_id', '') = goal_id),
    CONSTRAINT plan_versions_revision_non_negative CHECK (revision >= 0)
);

CREATE INDEX plan_versions_goal_idx ON plan_versions (goal_id);

-- ---------------------------------------------------------------------------
-- 诊断题
--
-- 题目是**只增不改**的：生成一次就固定（ADR-0021「不合格题目不外流」）。
--
-- **point_id 建不了外键**：知识点身份是课程内的（ADR-0016，主键是
-- (course_id, id)），而题目不记它属于哪门课——因此这里只能建索引，不能建外键。
-- 这是「知识点仍在课程内」这个决定的直接代价，记在 ADR-0026 的边界里。
-- ---------------------------------------------------------------------------

CREATE TABLE diagnostic_exercises (
    id       TEXT PRIMARY KEY,
    point_id TEXT,
    payload  JSONB NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT diagnostic_exercises_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT diagnostic_exercises_payload_version
        CHECK (COALESCE(jsonb_typeof(payload -> 'schema_version'), 'missing') = 'number'),
    CONSTRAINT diagnostic_exercises_payload_id CHECK (COALESCE(payload ->> 'id', '') = id),
    CONSTRAINT diagnostic_exercises_revision_non_negative CHECK (revision >= 0)
);

CREATE INDEX diagnostic_exercises_point_idx ON diagnostic_exercises (point_id);

COMMIT;
