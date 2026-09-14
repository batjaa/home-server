"use strict";

// Run with the pinned image's real UptimeCalculator, never a copied algorithm.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { replay, retentionCutoffs } = require("./rebuild-aggregates.cjs");
const now = Date.parse("2026-09-14T04:30:00Z") / 1000;
async function calculate(records, fixedNow = now) {
    const rows = [];
    const summary = await replay(records, { now: fixedNow, emit: (row) => rows.push(row) });
    return { rows, summary };
}

test("UTC day boundaries retain every sample under a non-UTC container timezone", async () => {
    const records = [];
    for (let i = 0; i < 2880; i++) {
        records.push([1, 1, 10, new Date(Date.parse("2026-09-10T00:00:00Z") + i * 60000).toISOString()]);
    }
    const { rows, summary } = await calculate(records);
    const daily = rows.filter((row) => row.table === "stat_daily");
    assert.equal(summary.records, 2880);
    assert.deepEqual(daily.map((row) => [row.timestamp, row.up, row.down]), [
        [Date.parse("2026-09-10T00:00:00Z") / 1000, 1440, 0],
        [Date.parse("2026-09-11T00:00:00Z") / 1000, 1440, 0],
    ]);
});

test("vendor pending, maintenance, null-ping, and per-monitor behavior is preserved", async () => {
    const { rows, summary } = await calculate([
        [1, 1, null, "2026-09-14 04:20:00.000"],
        [1, 1, 20, "2026-09-14 04:20:01.000"],
        [1, 2, null, "2026-09-14 04:20:02.000"],
        [1, 3, null, "2026-09-14 04:20:03.000"],
        [2, 1, 90, "2026-09-14 04:20:00.000"],
    ]);
    const daily = rows.filter((row) => row.table === "stat_daily");
    assert.deepEqual(daily.map((row) => [row.monitor_id, row.up, row.down, row.ping, row.ping_min, row.ping_max, row.extras]), [
        [1, 2, 1, 10, 0, 20, '{"maintenance":1}'],
        [2, 1, 0, 90, 90, 90, null],
    ]);
    assert.deepEqual(summary.statuses, { 1: 3, 2: 1, 3: 1 });
});

test("fixed retention cutoffs exclude old buckets and keep boundary buckets", async () => {
    const cutoffs = retentionCutoffs(now);
    const times = [...new Set(Object.values(cutoffs).flatMap((time) => [time - 60, time]))].sort((a, b) => a - b);
    const { rows } = await calculate(times.map((time) => [1, 1, 5, new Date(time * 1000).toISOString()]));
    for (const [table, cutoff] of Object.entries(cutoffs)) {
        const buckets = rows.filter((row) => row.table === table);
        assert.ok(buckets.every((row) => row.timestamp >= cutoff));
        assert.ok(buckets.some((row) => row.timestamp === cutoff));
    }
});

test("retained boundary buckets survive the vendor's bounded in-memory queues", async () => {
    const cutoffs = retentionCutoffs(now);
    const times = new Set();
    for (let time = cutoffs.stat_hourly; time <= now; time += 3600) {
        times.add(time);
    }
    for (let time = cutoffs.stat_minutely; time <= now; time += 60) {
        times.add(time);
    }
    const records = [...times].sort((a, b) => a - b).map((time) => [1, 1, 5, new Date(time * 1000).toISOString()]);
    const { rows } = await calculate(records);
    assert.equal(rows.filter((row) => row.table === "stat_hourly").length, 721);
    assert.equal(rows.filter((row) => row.table === "stat_minutely").length, 1441);
});

test("ordered input is enforced before crossing monitor or time boundaries", async () => {
    await assert.rejects(calculate([
        [1, 1, 10, "2026-09-14 04:20:02.000"],
        [1, 1, 10, "2026-09-14 04:20:01.000"],
    ]), /ordered/);
    await assert.rejects(calculate([
        [2, 1, 10, "2026-09-14 04:20:00.000"],
        [1, 1, 10, "2026-09-14 04:20:01.000"],
    ]), /ordered/);
    await assert.rejects(calculate([[1, 99, 10, "2026-09-14 04:20:00.000"]]), /unknown heartbeat status/);
    await assert.rejects(calculate([[1, 1, 10, "2026-09-15 04:20:00.000"]]), /after --now/);
});
