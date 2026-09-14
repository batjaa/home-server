#!/usr/bin/env node
"use strict";

// Offline recovery only: run inside the pinned Kuma image, never start its app.
// The vendor calculator supplies all uptime/ping arithmetic. TEST_BACKEND keeps
// it in memory; UTC parsing avoids the v1 migration's local-time bucket overlap.
const fs = require("node:fs");
const readline = require("node:readline");
const path = require("node:path");
const { once } = require("node:events");

const EXPECTED_KUMA_VERSION = "2.5.4";

function dependencies(root = process.env.UPTIME_KUMA_ROOT || "/app") {
    process.env.TEST_BACKEND = "1";
    const version = require(path.join(root, "package.json")).version;
    if (version !== EXPECTED_KUMA_VERSION) {
        throw new Error(`Expected Kuma ${EXPECTED_KUMA_VERSION}, found ${version}`);
    }
    const dayjs = require(path.join(root, "node_modules/dayjs"));
    dayjs.extend(require(path.join(root, "node_modules/dayjs/plugin/utc")));
    const util = require(path.join(root, "src/util"));
    // Vendor debug messages must not contaminate the JSONL output.
    util.log.debug = () => {};
    const { R } = require(path.join(root, "node_modules/redbean-node"));
    for (const method of ["store", "exec", "findOne", "find", "getAll", "getRow", "setup"]) {
        R[method] = () => { throw new Error(`Unexpected database access: ${method}`); };
    }
    const { UptimeCalculator } = require(path.join(root, "server/uptime-calculator"));
    return { dayjs, util, UptimeCalculator };
}

function retentionCutoffs(now) {
    if (!Number.isSafeInteger(now) || now <= 0) {
        throw new Error("--now must be a positive UTC epoch in whole seconds");
    }
    return {
        stat_daily: Math.floor(now / 86400) * 86400 - 364 * 86400,
        stat_hourly: Math.floor((now - 30 * 86400) / 3600) * 3600,
        stat_minutely: Math.floor((now - 86400) / 60) * 60,
    };
}

async function replay(records, { now, emit, root }) {
    const cutoffs = retentionCutoffs(now);
    const { dayjs, util, UptimeCalculator } = dependencies(root);
    const validStatuses = new Set([util.UP, util.DOWN, util.PENDING, util.MAINTENANCE]);
    const definitions = [
        ["stat_daily", "dailyUptimeDataList", "getDailyKey"],
        ["stat_hourly", "hourlyUptimeDataList", "getHourlyKey"],
        ["stat_minutely", "minutelyUptimeDataList", "getMinutelyKey"],
    ];
    let calculator;
    let monitorID;
    let previousMillis = -Infinity;
    let pending = {};
    const summary = { records: 0, monitors: 0, statuses: {}, buckets: {} };

    async function flush(table) {
        const bucket = pending[table];
        if (!bucket || bucket.timestamp < cutoffs[table]) {
            return;
        }
        const { up, down, avgPing, minPing, maxPing, ...extras } = bucket.data;
        await emit({
            table, monitor_id: monitorID, timestamp: bucket.timestamp,
            up, down, ping: avgPing, ping_min: minPing, ping_max: maxPing,
            extras: Object.keys(extras).length ? JSON.stringify(extras) : null,
        });
        summary.buckets[table] = (summary.buckets[table] || 0) + 1;
    }

    for await (const record of records) {
        if (!Array.isArray(record) || record.length !== 4) {
            throw new Error("Each input row must be [monitor_id, status, ping, UTC time]");
        }
        const [id, status, ping, time] = record;
        if (!Number.isSafeInteger(id) || id <= 0 || !validStatuses.has(status)) {
            throw new Error("Invalid monitor ID or unknown heartbeat status");
        }
        if (typeof time !== "string" || !/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d{1,3})?Z?$/.test(time)) {
            throw new Error("Heartbeat time must be a stored UTC timestamp");
        }
        const date = dayjs.utc(time);
        const millis = date.valueOf();
        if (!date.isValid() || millis > now * 1000 + 999) {
            throw new Error("Invalid heartbeat timestamp or timestamp after --now");
        }
        if (monitorID !== undefined && (id < monitorID || (id === monitorID && millis < previousMillis))) {
            throw new Error("Input must be ordered by monitor_id, time, and source ID");
        }
        if (id !== monitorID) {
            for (const [table] of definitions) {
                await flush(table);
            }
            monitorID = id;
            calculator = new UptimeCalculator();
            calculator.monitorID = id;
            calculator.setMigrationMode(true);
            pending = {};
            summary.monitors += 1;
        }
        const keys = {};
        for (const [table, , keyMethod] of definitions) {
            keys[table] = calculator[keyMethod](date, false);
            if (pending[table] && pending[table].timestamp !== keys[table]) {
                await flush(table);
            }
        }
        // Exactly the vendor's conversion; null ping becomes NaN, not zero.
        await calculator.update(status, parseFloat(ping), date);
        for (const [table, queue] of definitions) {
            pending[table] = { timestamp: keys[table], data: calculator[queue][keys[table]] };
        }
        previousMillis = millis;
        summary.records += 1;
        summary.statuses[status] = (summary.statuses[status] || 0) + 1;
    }
    for (const [table] of definitions) {
        await flush(table);
    }
    return summary;
}

async function* readRecords(input) {
    const lines = readline.createInterface({ input, crlfDelay: Infinity });
    let number = 0;
    for await (const line of lines) {
        number += 1;
        if (!line.trim()) {
            continue;
        }
        try {
            yield JSON.parse(line);
        } catch {
            throw new Error(`Invalid JSON at input line ${number}`);
        }
    }
}

async function main(args) {
    if (args.length === 1 && ["version", "--version"].includes(args[0])) {
        process.stdout.write(`${process.env.REBUILD_VERSION || "dev"} (Kuma ${EXPECTED_KUMA_VERSION})\n`);
        return;
    }
    if (args.length !== 4 || args[0] !== "--input" || args[2] !== "--now") {
        throw new Error("Usage: rebuild-aggregates.cjs --input <JSONL path or -> --now <UTC epoch seconds>; version");
    }
    const input = args[1] === "-" ? process.stdin : fs.createReadStream(args[1]);
    const summary = await replay(readRecords(input), {
        now: Number(args[3]),
        emit: async (row) => {
            if (!process.stdout.write(JSON.stringify(row) + "\n")) {
                await once(process.stdout, "drain");
            }
        },
    });
    process.stderr.write(JSON.stringify(summary) + "\n");
}

module.exports = { replay, retentionCutoffs };
if (require.main === module) {
    main(process.argv.slice(2)).catch((error) => {
        process.stderr.write(error.message + "\n");
        process.exitCode = 1;
    });
}
