(function (root) {
    'use strict';

    function formatDuration(elapsedMs) {
        const totalSeconds = Math.max(0, Math.floor((Number(elapsedMs) || 0) / 1000));
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;
        return [hours, minutes, seconds]
            .map(value => String(value).padStart(2, '0'))
            .join(':');
    }

    function createElapsedTimer(options = {}) {
        const now = typeof options.now === 'function' ? options.now : () => Date.now();
        const schedule = typeof options.setInterval === 'function' ? options.setInterval : setInterval;
        const cancelSchedule = typeof options.clearInterval === 'function' ? options.clearInterval : clearInterval;
        const onTick = typeof options.onTick === 'function' ? options.onTick : () => {};
        const intervalMs = Math.max(250, Number(options.intervalMs) || 1000);
        let startedAt = 0;
        let finishedAt = 0;
        let active = false;
        let status = 'idle';
        let intervalId = null;

        function elapsedAt(referenceAt = now()) {
            if (!startedAt) return 0;
            const end = active ? Number(referenceAt) || now() : finishedAt || Number(referenceAt) || now();
            return Math.max(0, end - startedAt);
        }

        function snapshot(referenceAt = now()) {
            const elapsedMs = elapsedAt(referenceAt);
            return {
                active,
                status,
                startedAt,
                finishedAt,
                elapsedMs,
                formatted: formatDuration(elapsedMs)
            };
        }

        function emit(referenceAt = now()) {
            const current = snapshot(referenceAt);
            onTick(current);
            return current;
        }

        function clearTicker() {
            if (intervalId !== null) cancelSchedule(intervalId);
            intervalId = null;
        }

        function start(startOptions = {}) {
            clearTicker();
            const requestedAt = Number(startOptions.startedAt);
            startedAt = requestedAt > 0 ? requestedAt : now();
            finishedAt = 0;
            active = true;
            status = String(startOptions.status || 'running').toLowerCase();
            const current = emit(startedAt);
            intervalId = schedule(() => emit(now()), intervalMs);
            return current;
        }

        function finish(finishOptions = {}) {
            if (!startedAt) return snapshot();
            if (!active && finishedAt) return snapshot(finishedAt);
            const requestedAt = Number(finishOptions.finishedAt);
            finishedAt = Math.max(startedAt, requestedAt > 0 ? requestedAt : now());
            active = false;
            status = String(finishOptions.status || 'done').toLowerCase();
            clearTicker();
            return emit(finishedAt);
        }

        function reset() {
            clearTicker();
            startedAt = 0;
            finishedAt = 0;
            active = false;
            status = 'idle';
            return emit(0);
        }

        return {
            start,
            finish,
            reset,
            snapshot,
            formatDuration
        };
    }

    root.FavoritosElapsedTimer = {
        create: createElapsedTimer,
        formatDuration
    };
})(typeof window !== 'undefined' ? window : globalThis);
