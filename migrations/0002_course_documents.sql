-- 0002_course_documents.sql —— 课程文档（ADR-0028）
--
-- 一行一课程文档。沿用 0001 的三条原则：
--
-- 1. **身份与外键用列，内层形状用 JSONB。** 文档 id 由来源推导（可复现），课程
--    归属是跨聚合引用，因此用列 + 外键；知识点关联是**内层列表**（随文档整体
--    替换），因此用 JSONB。
-- 2. **只存事实，不存派生值。** 材料种类由扩展名推断得出，但那是**导入时**的判
--    断，写进库里就成了第二份真相——所以库里存的是当时判定的结果（一个字段），
--    而不是一个可以随时重算的函数。
-- 3. **负载必须带 schema_version。**
--
-- **正文可能很大**（整本讲义、整本 PDF 的文本），因此用 TEXT 而不是 JSONB——
-- 它不需要参与查询。

BEGIN;

CREATE TABLE course_documents (
    id         TEXT PRIMARY KEY,
    course_id  TEXT NOT NULL REFERENCES courses (id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    kind       TEXT NOT NULL,
    source     TEXT NOT NULL,
    content    TEXT NOT NULL,
    point_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    payload    JSONB NOT NULL,
    revision   INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT course_documents_kind
        CHECK (kind IN ('notes', 'exercise', 'code', 'notebook', 'reference', 'other')),
    CONSTRAINT course_documents_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT course_documents_payload_version
        CHECK (COALESCE(jsonb_typeof(payload -> 'schema_version'), 'missing') = 'number'),
    CONSTRAINT course_documents_payload_id CHECK (COALESCE(payload ->> 'id', '') = id),
    CONSTRAINT course_documents_payload_course
        CHECK (COALESCE(payload ->> 'course_id', '') = course_id),
    CONSTRAINT course_documents_point_ids_array CHECK (jsonb_typeof(point_ids) = 'array'),
    CONSTRAINT course_documents_revision_non_negative CHECK (revision >= 0)
);

CREATE INDEX course_documents_course_idx ON course_documents (course_id);

-- **同一份材料在系统里只有一个身份**：文档 id 由来源推导（可复现），主键因此
-- 已经保证了「一个来源 = 一份文档」——重复导入是**覆盖**，不是追加。课程归属是
-- 文档的属性，因此同一份材料被导入到另一门课会被摄取用例**拒绝并报告**（材料
-- 属于一门课）。

COMMIT;
