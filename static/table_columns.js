// JK Sistema - Tabelas com colunas redimensionáveis, cabeçalho centralizado e quebra automática.
(function initTableColumnsGlobal() {
	if (window.__jkTableColumnsInit) return;
	window.__jkTableColumnsInit = true;

	const STORAGE_PREFIX = 'jk_table_widths_v1:';
	const MIN_WIDTH = 12;

	function isWidthStorageEnabled(table) {
		return String(table?.getAttribute('data-jk-width-storage') || '').toLowerCase() !== 'off';
	}

	function ensureStyle() {
		if (document.getElementById('jk-table-columns-style')) return;
		const style = document.createElement('style');
		style.id = 'jk-table-columns-style';
		style.textContent = `
			.jk-table-enhanced thead th {
				position: sticky;
				overflow: hidden;
				white-space: normal !important;
				word-break: normal !important;
				overflow-wrap: anywhere !important;
				hyphens: auto;
				vertical-align: middle;
				text-align: center !important;
				user-select: none;
				box-sizing: border-box;
			}
			.jk-table-enhanced thead th .jk-th-label {
				display: block;
				width: 100%;
				text-align: center;
				white-space: normal !important;
				word-break: normal !important;
				overflow-wrap: anywhere !important;
				hyphens: auto;
			}
			.jk-table-enhanced .jk-resize-handle {
				position: absolute;
				right: 0;
				top: 0;
				width: 6px;
				height: 100%;
				cursor: col-resize;
				background: transparent;
				z-index: 2;
			}
			.jk-table-enhanced .jk-resize-handle:hover,
			.jk-table-enhanced .jk-resize-handle.dragging {
				background: rgba(79, 172, 254, 0.45);
			}
			.jk-row-clickable {
				cursor: pointer;
			}
		`;
		document.head.appendChild(style);
	}

	function getStorageKey(table) {
		const pageKey = location.pathname.replace(/[^a-z0-9]+/gi, '_').toLowerCase();
		const tableId = table.id || table.getAttribute('data-table-key') || Array.from(document.querySelectorAll('table')).indexOf(table);
		return `${STORAGE_PREFIX}${pageKey}:${tableId}`;
	}

	function loadWidths(table, count) {
		try {
			const raw = localStorage.getItem(getStorageKey(table));
			if (!raw) return null;
			const parsed = JSON.parse(raw);
			return Array.isArray(parsed) && parsed.length === count ? parsed : null;
		} catch (_e) {
			return null;
		}
	}

	function saveWidths(table, ths) {
		try {
			const widths = Array.from(ths || []).map((th) => Math.round(th.offsetWidth || 0));
			localStorage.setItem(getStorageKey(table), JSON.stringify(widths));
		} catch (_e) {
			// silencioso
		}
	}

	function getBodyRows(table) {
		return Array.from(table.querySelectorAll('tbody tr'));
	}

	function applyWidth(table, index, width) {
		const safeWidth = Math.max(MIN_WIDTH, Math.round(width || MIN_WIDTH));
		const th = table.querySelector(`thead tr th:nth-child(${index + 1})`);
		if (th) {
			th.style.width = `${safeWidth}px`;
			th.style.minWidth = `${safeWidth}px`;
			th.style.maxWidth = `${safeWidth}px`;
		}
		getBodyRows(table).forEach((row) => {
			const td = row.children[index];
			if (td) {
				td.style.width = `${safeWidth}px`;
				td.style.minWidth = `${safeWidth}px`;
				td.style.maxWidth = `${safeWidth}px`;
			}
		});
	}

	function prepareHeaderCell(th) {
		if (!th) return;
		th.style.textAlign = 'center';
		th.style.whiteSpace = 'normal';
		th.style.wordBreak = 'normal';
		th.style.overflowWrap = 'anywhere';
		th.style.hyphens = 'auto';
		if (!th.querySelector('.jk-th-label') && !th.querySelector('.th-content')) {
			const label = document.createElement('span');
			label.className = 'jk-th-label';
			label.innerHTML = th.innerHTML;
			th.innerHTML = '';
			th.appendChild(label);
		}
	}

	function enhanceTable(table) {
		if (!table || table.dataset.jkColumnsEnhanced === '1') return;
		if (table.dataset.jkNoEnhance === '1') return;
		const ths = Array.from(table.querySelectorAll('thead tr th'));
		if (!ths.length) return;
		if (table.querySelector('.th-resizer')) return;

		ensureStyle();
		table.dataset.jkColumnsEnhanced = '1';
		table.classList.add('jk-table-enhanced');

		ths.forEach((th) => prepareHeaderCell(th));

		requestAnimationFrame(() => {
			const widths = isWidthStorageEnabled(table) ? loadWidths(table, ths.length) : null;
			ths.forEach((th, idx) => {
				const naturalWidth = widths && widths[idx] ? widths[idx] : Math.round(th.offsetWidth || 120);
				applyWidth(table, idx, naturalWidth);

				if (th.querySelector('.jk-resize-handle')) return;
				const handle = document.createElement('div');
				handle.className = 'jk-resize-handle';
				th.appendChild(handle);

				let startX = 0;
				let startW = 0;

				handle.addEventListener('mousedown', (e) => {
					e.preventDefault();
					e.stopPropagation();
					startX = e.clientX;
					startW = th.offsetWidth;
					const minDragWidth = Math.max(MIN_WIDTH, Math.round(startW * 0.3));
					handle.classList.add('dragging');
					document.body.style.cursor = 'col-resize';
					document.body.style.userSelect = 'none';

					const onMove = (moveEvent) => {
						const newWidth = Math.max(minDragWidth, startW + (moveEvent.clientX - startX));
						applyWidth(table, idx, newWidth);
					};

					const onUp = () => {
						handle.classList.remove('dragging');
						document.body.style.cursor = '';
						document.body.style.userSelect = '';
						document.removeEventListener('mousemove', onMove);
						document.removeEventListener('mouseup', onUp);
						if (isWidthStorageEnabled(table)) {
							saveWidths(table, ths);
						}
					};

					document.addEventListener('mousemove', onMove);
					document.addEventListener('mouseup', onUp);
				});

				handle.addEventListener('dblclick', (e) => {
					e.preventDefault();
					e.stopPropagation();
					applyWidth(table, idx, MIN_WIDTH);
					if (isWidthStorageEnabled(table)) {
						saveWidths(table, ths);
					}
				});
			});
		});
	}

	function scanTables(root) {
		Array.from((root || document).querySelectorAll('table')).forEach((table) => enhanceTable(table));
	}

	function startObserver() {
		const observer = new MutationObserver((mutations) => {
			for (const mutation of mutations) {
				mutation.addedNodes.forEach((node) => {
					if (!(node instanceof HTMLElement)) return;
					if (node.matches && node.matches('table')) enhanceTable(node);
					scanTables(node);
				});
			}
		});
		observer.observe(document.body, { childList: true, subtree: true });
	}

	function init() {
		scanTables(document);
		startObserver();
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', init, { once: true });
	} else {
		init();
	}

	window.JKTableColumns = {
		enhanceTable,
		scanTables,
	};
})();
