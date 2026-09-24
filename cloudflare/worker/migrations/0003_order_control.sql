-- stage-cloud-28：订单控制（§ 暂停/恢复/优先级）
-- priority：入队/claim 排序权重（越大越先）；control：运行控制信令（''|'paused'）
ALTER TABLE orders ADD COLUMN priority INTEGER DEFAULT 0;
ALTER TABLE orders ADD COLUMN control TEXT DEFAULT '';
