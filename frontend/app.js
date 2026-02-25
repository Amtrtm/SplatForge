/* ============================================================
   SplatForge -- Frontend Application (app.js)

   Pure vanilla JavaScript -- no dependencies.
   Handles: file upload, SSE streaming, pipeline control,
   live training stats, PSNR chart, 3D viewer, downloads.
   ============================================================ */

// ── Core State ──────────────────────────────────────────────
let currentJobId = null;
let eventSource = null;
let psnrHistory = [];          // [{iteration, psnr}]
let stageStartTimes = {};      // stage_name -> Date.now()
let currentStage = null;
let stageTimerInterval = null;
let uploadedFileName = null;   // track the uploaded file name

// ── Stage definitions (must match data-stage attributes) ────
const STAGES = ['analyzing', 'processing', 'training', 'exporting', 'converting'];

// ── Formatting Helpers ──────────────────────────────────────

function formatNumber(n) {
    return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function formatTime(seconds) {
    seconds = Math.round(seconds);
    if (seconds < 0) seconds = 0;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return h + ':' + m.toString().padStart(2, '0') + ':' + s.toString().padStart(2, '0');
    return m + ':' + s.toString().padStart(2, '0');
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
}

// ── Initialization ──────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function () {
    // 1. Fetch GPU info
    fetchGPUInfo();

    // 2. Set up drag & drop on #drop-zone
    var dropZone = document.getElementById('drop-zone');
    var fileInput = document.getElementById('file-input');

    dropZone.addEventListener('dragover', function (e) {
        e.preventDefault();
        dropZone.classList.add('drag-over');
    });

    dropZone.addEventListener('dragenter', function (e) {
        e.preventDefault();
        dropZone.classList.add('drag-over');
    });

    dropZone.addEventListener('dragleave', function () {
        dropZone.classList.remove('drag-over');
    });

    dropZone.addEventListener('drop', function (e) {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            handleFile(e.dataTransfer.files[0]);
        }
    });

    // 3. Click on drop zone triggers file input
    dropZone.addEventListener('click', function () {
        fileInput.click();
    });

    // 4. File input change
    fileInput.addEventListener('change', function () {
        if (this.files && this.files.length > 0) {
            handleFile(this.files[0]);
        }
    });

    // 5. Start button
    document.getElementById('start-btn').addEventListener('click', function () {
        startPipeline();
    });

    // 6. Cancel button
    document.getElementById('cancel-btn').addEventListener('click', function () {
        cancelPipeline();
    });

    // 7. Viewer toggle
    document.getElementById('viewer-toggle').addEventListener('click', function () {
        var iframe = document.getElementById('viewer-iframe');
        var btn = document.getElementById('viewer-toggle');
        if (iframe.style.display === 'none') {
            iframe.style.display = 'block';
            btn.textContent = 'Hide';
        } else {
            iframe.style.display = 'none';
            btn.textContent = 'Show';
        }
    });
});

// ── GPU Info ────────────────────────────────────────────────

function fetchGPUInfo() {
    fetch('/api/gpu-info')
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
            var badge = document.getElementById('gpu-badge');
            if (data.name && data.name !== 'No GPU detected') {
                badge.textContent = 'GPU: ' + data.name;
            } else {
                badge.textContent = 'No GPU';
                badge.style.color = 'var(--accent-amber)';
            }
        })
        .catch(function () {
            document.getElementById('gpu-badge').textContent = 'GPU: unknown';
        });
}

// ── File Upload ─────────────────────────────────────────────

function handleFile(file) {
    // 1. Validate extension
    var parts = file.name.split('.');
    var ext = '.' + parts[parts.length - 1].toLowerCase();
    var allowed = ['.mp4', '.mov', '.avi', '.mkv'];
    if (allowed.indexOf(ext) === -1) {
        showError('Unsupported file format. Allowed: MP4, MOV, AVI, MKV');
        return;
    }

    uploadedFileName = file.name;

    // 2. Show uploading state on drop zone
    var dropZone = document.getElementById('drop-zone');
    dropZone.innerHTML =
        '<div class="drop-zone-content">' +
            '<div class="drop-icon">' +
                '<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' +
                    '<path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>' +
                    '<polyline points="17 8 12 3 7 8"/>' +
                    '<line x1="12" y1="3" x2="12" y2="15"/>' +
                '</svg>' +
            '</div>' +
            '<p class="drop-label">' + escapeHtml(file.name) + '</p>' +
            '<p class="drop-hint">Uploading... 0%</p>' +
            '<div class="upload-progress-bar" style="width:100%;height:4px;background:var(--border);border-radius:2px;margin-top:8px;overflow:hidden;">' +
                '<div id="upload-progress-fill" style="width:0%;height:100%;background:var(--accent-blue);border-radius:2px;transition:width 0.2s;"></div>' +
            '</div>' +
        '</div>';

    // 3. Upload via XHR for progress tracking
    var formData = new FormData();
    formData.append('file', file);

    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload');

    xhr.upload.onprogress = function (e) {
        if (e.lengthComputable) {
            var pct = Math.round(e.loaded / e.total * 100);
            var hint = dropZone.querySelector('.drop-hint');
            if (hint) hint.textContent = 'Uploading... ' + pct + '%';
            var fill = document.getElementById('upload-progress-fill');
            if (fill) fill.style.width = pct + '%';
        }
    };

    xhr.onload = function () {
        if (xhr.status === 200) {
            var data;
            try {
                data = JSON.parse(xhr.responseText);
            } catch (e) {
                showError('Upload failed: invalid server response');
                resetDropZone();
                return;
            }
            currentJobId = data.job_id;
            showVideoInfo(data.video_info);
            showUploadSuccess(file.name);
            document.getElementById('start-btn').disabled = false;
        } else {
            var errMsg = 'Upload failed';
            try {
                var errData = JSON.parse(xhr.responseText);
                errMsg = 'Upload failed: ' + (errData.detail || xhr.responseText);
            } catch (e) {
                errMsg = 'Upload failed: ' + xhr.responseText;
            }
            showError(errMsg);
            resetDropZone();
        }
    };

    xhr.onerror = function () {
        showError('Upload failed -- network error');
        resetDropZone();
    };

    xhr.send(formData);
}

function showUploadSuccess(filename) {
    var dropZone = document.getElementById('drop-zone');
    dropZone.innerHTML =
        '<div class="drop-zone-content">' +
            '<div class="drop-icon" style="color:var(--accent-green);">' +
                '<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' +
                    '<path d="M22 11.08V12a10 10 0 11-5.93-9.14"/>' +
                    '<polyline points="22 4 12 14.01 9 11.01"/>' +
                '</svg>' +
            '</div>' +
            '<p class="drop-label">' + escapeHtml(filename) + '</p>' +
            '<p class="drop-hint" style="color:var(--accent-green);">Ready to process</p>' +
        '</div>';
}

function resetDropZone() {
    var dropZone = document.getElementById('drop-zone');
    dropZone.innerHTML =
        '<div class="drop-zone-content">' +
            '<div class="drop-icon">' +
                '<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' +
                    '<rect x="2" y="6" width="15" height="12" rx="2"/>' +
                    '<path d="M17 10l4-2v8l-4-2"/>' +
                '</svg>' +
            '</div>' +
            '<p class="drop-label">Drop video here</p>' +
            '<p class="drop-hint">MP4, MOV, AVI, MKV</p>' +
        '</div>' +
        '<input type="file" id="file-input" accept=".mp4,.mov,.avi,.mkv" hidden>';

    // Re-attach file input listener
    var fileInput = document.getElementById('file-input');
    fileInput.addEventListener('change', function () {
        if (this.files && this.files.length > 0) {
            handleFile(this.files[0]);
        }
    });
}

// ── Video Info Display ──────────────────────────────────────

function showVideoInfo(info) {
    var panel = document.getElementById('video-info');
    panel.hidden = false;
    document.getElementById('info-duration').textContent = formatTime(info.duration);
    document.getElementById('info-resolution').textContent = info.resolution;
    document.getElementById('info-fps').textContent = info.fps + ' fps';
    document.getElementById('info-size').textContent = formatBytes(info.file_size);
}

// ── Pipeline Control ────────────────────────────────────────

async function startPipeline() {
    if (!currentJobId) {
        showError('No video uploaded');
        return;
    }

    var preset = document.getElementById('preset-select').value;

    try {
        var resp = await fetch('/api/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_id: currentJobId, preset: preset })
        });

        if (!resp.ok) {
            var errData;
            try {
                errData = await resp.json();
            } catch (e) {
                errData = { detail: 'Unknown error' };
            }
            showError('Failed to start: ' + (errData.detail || resp.statusText));
            return;
        }
    } catch (e) {
        showError('Failed to start pipeline -- network error');
        return;
    }

    // UI state changes
    document.getElementById('start-btn').hidden = true;
    document.getElementById('cancel-btn').hidden = false;
    document.getElementById('preset-select').disabled = true;
    document.getElementById('progress-stage-label').textContent = 'Starting...';

    // Reset state
    psnrHistory = [];
    stageStartTimes = {};
    currentStage = null;

    // Hide any previous download section
    document.getElementById('download-section').hidden = true;

    // Clear previous error banners
    var existingError = document.querySelector('.error-banner');
    if (existingError) existingError.remove();

    // Reset stats panel
    document.getElementById('stats-panel').hidden = true;
    document.getElementById('training-progress').hidden = true;
    document.getElementById('psnr-chart').hidden = true;

    // Reset stage items
    document.querySelectorAll('.stage-item').forEach(function (item) {
        item.classList.remove('active', 'complete', 'error');
        item.querySelector('.stage-icon').innerHTML = '<span class="stage-dot"></span>';
        var timeSpan = item.querySelector('.stage-time');
        if (timeSpan) timeSpan.textContent = '';
    });

    // Reset progress ring
    updateOverallProgress(0);

    // Reset log
    document.getElementById('log-output').textContent = '';

    // Connect SSE
    connectSSE();
}

async function cancelPipeline() {
    if (!currentJobId) return;

    try {
        var resp = await fetch('/api/cancel/' + currentJobId, { method: 'POST' });
        if (!resp.ok) {
            showError('Failed to cancel pipeline');
        }
    } catch (e) {
        showError('Cancel request failed -- network error');
    }
    // SSE handler will pick up the "cancelled" stage event
}

// ── SSE Connection ──────────────────────────────────────────

function connectSSE() {
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }

    eventSource = new EventSource('/api/status/stream?job_id=' + currentJobId);

    eventSource.onmessage = function (event) {
        var data;
        try {
            data = JSON.parse(event.data);
        } catch (e) {
            console.warn('SSE: failed to parse event data', event.data);
            return;
        }
        handleSSEMessage(data);
    };

    eventSource.onerror = function () {
        // SSE will attempt to reconnect automatically.
        // Only log a warning -- do not close the connection here.
        console.warn('SSE connection error -- browser will attempt reconnect');
    };
}

// ── SSE Message Handler ─────────────────────────────────────

function handleSSEMessage(data) {
    // 1. Update stage list
    if (data.stage) {
        updateStageList(data.stage, data.stage_index);

        // Update stage label
        var stageLabels = {
            'analyzing': 'Analyzing Video',
            'processing': 'COLMAP Processing',
            'training': 'Training Gaussian Splat',
            'exporting': 'Exporting .ply',
            'converting': 'Converting .ksplat',
            'complete': 'Complete!',
            'error': 'Error',
            'cancelled': 'Cancelled'
        };
        var label = stageLabels[data.stage] || data.stage;
        document.getElementById('progress-stage-label').textContent = label;
    }

    // 2. Update overall progress ring
    if (data.overall_progress != null) {
        updateOverallProgress(data.overall_progress);
    }

    // 3. Update message
    if (data.message) {
        document.getElementById('progress-message').textContent = data.message;
        appendLog(data.message);
    }

    // 4. Update stats (during training)
    if (data.stats && typeof data.stats === 'object' && Object.keys(data.stats).length > 0) {
        updateStats(data.stats);
    }

    // 5. Show viewer if ready
    if (data.viewer_ready && data.viewer_url) {
        showViewer(data.viewer_url);
    }

    // 6. Handle terminal states
    if (data.stage === 'complete') {
        onPipelineComplete();
    }
    if (data.stage === 'error') {
        showError(data.message || 'Pipeline failed');
        onPipelineEnd();
    }
    if (data.stage === 'cancelled') {
        onPipelineEnd();
    }
}

// ── Stage List Update ───────────────────────────────────────

function updateStageList(stage, stageIndex) {
    var stageItems = document.querySelectorAll('.stage-item');

    // Track stage timing
    if (stage !== currentStage && STAGES.indexOf(stage) !== -1) {
        currentStage = stage;
        stageStartTimes[stage] = Date.now();
        startStageTimer();
    }

    stageItems.forEach(function (item) {
        var itemStage = item.dataset.stage;
        var icon = item.querySelector('.stage-icon');

        item.classList.remove('active', 'complete', 'error');

        var itemIdx = STAGES.indexOf(itemStage);
        var currentIdx = STAGES.indexOf(stage);

        if (itemIdx !== -1 && currentIdx !== -1 && itemIdx < currentIdx) {
            // Completed stage
            item.classList.add('complete');
            icon.innerHTML = '<span class="checkmark">\u2713</span>';
        } else if (itemStage === stage) {
            // Active stage
            item.classList.add('active');
            icon.innerHTML = '<span class="spinner"></span>';
        } else {
            // Pending stage -- reset to dot
            icon.innerHTML = '<span class="stage-dot"></span>';
        }
    });
}

// ── Stage Timer ─────────────────────────────────────────────

function startStageTimer() {
    if (stageTimerInterval) clearInterval(stageTimerInterval);
    stageTimerInterval = setInterval(function () {
        var timeSpans = document.querySelectorAll('.stage-time');
        timeSpans.forEach(function (span) {
            var stage = span.dataset.stage;
            if (stageStartTimes[stage]) {
                var elapsed = (Date.now() - stageStartTimes[stage]) / 1000;
                span.textContent = formatTime(elapsed);
            }
        });
    }, 1000);
}

// ── Stats Update ────────────────────────────────────────────

function updateStats(stats) {
    var panel = document.getElementById('stats-panel');
    if (panel.hidden) {
        panel.hidden = false;
        panel.style.animation = 'fade-up 0.3s ease';
    }

    // Show training progress bar
    var progressContainer = document.getElementById('training-progress');
    if (progressContainer.hidden) progressContainer.hidden = false;

    // Update each stat
    if (stats.iteration != null) {
        var maxIter = stats.max_iterations || 30000;
        updateStatValue('stat-iteration', formatNumber(stats.iteration) + ' / ' + formatNumber(maxIter));
        // Update training progress bar
        var pct = (stats.iteration / maxIter) * 100;
        document.getElementById('training-progress-fill').style.width = Math.min(pct, 100) + '%';
    }
    if (stats.psnr != null) {
        updateStatValue('stat-psnr', stats.psnr.toFixed(1) + ' <span class="stat-unit">dB</span>');
        // Push to PSNR history for chart
        psnrHistory.push({ iteration: stats.iteration || 0, psnr: stats.psnr });
        drawPSNRChart();
    }
    if (stats.loss != null) {
        updateStatValue('stat-loss', stats.loss < 0.001 ? stats.loss.toExponential(2) : stats.loss.toFixed(4));
    }
    if (stats.num_gaussians != null) {
        updateStatValue('stat-gaussians', formatNumber(stats.num_gaussians));
    }
    if (stats.train_rays_per_sec != null) {
        updateStatValue('stat-speed', formatNumber(stats.train_rays_per_sec) + ' <span class="stat-unit">rays/s</span>');
    }
    if (stats.eta_seconds != null) {
        updateStatValue('stat-eta', formatTime(stats.eta_seconds));
    }

    // Show PSNR chart
    var chart = document.getElementById('psnr-chart');
    if (chart.hidden) chart.hidden = false;
}

function updateStatValue(elementId, html) {
    var el = document.getElementById(elementId);
    if (el) el.innerHTML = html;
}

// ── Overall Progress Ring ───────────────────────────────────

function updateOverallProgress(fraction) {
    var circumference = 2 * Math.PI * 34;  // r=34 from SVG
    var offset = circumference * (1 - fraction);
    var fill = document.getElementById('progress-ring-fill');
    var text = document.getElementById('progress-ring-text');
    if (fill) fill.style.strokeDashoffset = offset;
    if (text) text.textContent = Math.round(fraction * 100) + '%';
}

// ── PSNR Chart (Canvas) ────────────────────────────────────

function drawPSNRChart() {
    var canvas = document.getElementById('psnr-chart');
    if (!canvas || !canvas.getContext) return;

    var ctx = canvas.getContext('2d');

    // Set canvas size accounting for DPI
    var rect = canvas.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    var W = rect.width;
    var H = rect.height;

    // Clear
    ctx.clearRect(0, 0, W, H);

    if (psnrHistory.length < 2) return;

    // Axis ranges
    var padding = { top: 20, right: 20, bottom: 30, left: 50 };
    var plotW = W - padding.left - padding.right;
    var plotH = H - padding.top - padding.bottom;

    var maxIter = 1;
    var psnrValues = [];
    for (var i = 0; i < psnrHistory.length; i++) {
        if (psnrHistory[i].iteration > maxIter) maxIter = psnrHistory[i].iteration;
        psnrValues.push(psnrHistory[i].psnr);
    }

    var minPSNR = Math.floor(Math.min.apply(null, psnrValues) / 5) * 5;
    var maxPSNR = Math.ceil(Math.max.apply(null, psnrValues) / 5) * 5;
    var psnrRange = maxPSNR - minPSNR;
    if (psnrRange === 0) psnrRange = 5;

    // Ensure at least some visible range
    if (maxPSNR === minPSNR) {
        maxPSNR = minPSNR + 5;
        psnrRange = 5;
    }

    // Helper to convert data to canvas coords
    function toX(iter) { return padding.left + (iter / maxIter) * plotW; }
    function toY(psnr) { return padding.top + plotH - ((psnr - minPSNR) / psnrRange) * plotH; }

    // Draw grid lines at 5 dB increments
    ctx.strokeStyle = 'rgba(42, 42, 62, 0.8)';
    ctx.lineWidth = 1;
    ctx.font = '10px JetBrains Mono, monospace';
    ctx.fillStyle = '#888899';
    ctx.textAlign = 'left';
    for (var db = minPSNR; db <= maxPSNR; db += 5) {
        var y = toY(db);
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(W - padding.right, y);
        ctx.stroke();
        ctx.fillText(db + ' dB', 4, y + 4);
    }

    // Draw iteration axis labels
    ctx.textAlign = 'center';
    var iterStep = Math.ceil(maxIter / 5 / 1000) * 1000;
    if (iterStep < 1000) iterStep = 1000;
    for (var it = 0; it <= maxIter; it += iterStep) {
        var x = toX(it);
        if (x >= padding.left && x <= W - padding.right) {
            ctx.fillText(formatNumber(it), x, H - 8);
        }
    }

    // Draw gradient fill under the line
    var gradient = ctx.createLinearGradient(0, padding.top, 0, padding.top + plotH);
    gradient.addColorStop(0, 'rgba(0, 170, 255, 0.15)');
    gradient.addColorStop(1, 'rgba(0, 170, 255, 0.0)');

    ctx.beginPath();
    ctx.moveTo(toX(psnrHistory[0].iteration), toY(psnrHistory[0].psnr));
    for (var i = 1; i < psnrHistory.length; i++) {
        var prev = psnrHistory[i - 1];
        var curr = psnrHistory[i];
        var cpX = (toX(prev.iteration) + toX(curr.iteration)) / 2;
        ctx.quadraticCurveTo(cpX, toY(prev.psnr), toX(curr.iteration), toY(curr.psnr));
    }
    // Close the fill path along the bottom
    ctx.lineTo(toX(psnrHistory[psnrHistory.length - 1].iteration), padding.top + plotH);
    ctx.lineTo(toX(psnrHistory[0].iteration), padding.top + plotH);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    // Draw glow effect (wider, semi-transparent line)
    ctx.strokeStyle = 'rgba(0, 170, 255, 0.2)';
    ctx.lineWidth = 6;
    ctx.beginPath();
    ctx.moveTo(toX(psnrHistory[0].iteration), toY(psnrHistory[0].psnr));
    for (var i = 1; i < psnrHistory.length; i++) {
        var prev = psnrHistory[i - 1];
        var curr = psnrHistory[i];
        var cpX = (toX(prev.iteration) + toX(curr.iteration)) / 2;
        ctx.quadraticCurveTo(cpX, toY(prev.psnr), toX(curr.iteration), toY(curr.psnr));
    }
    ctx.stroke();

    // Draw main PSNR line with bezier smoothing
    ctx.strokeStyle = '#00aaff';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(toX(psnrHistory[0].iteration), toY(psnrHistory[0].psnr));
    for (var i = 1; i < psnrHistory.length; i++) {
        var prev = psnrHistory[i - 1];
        var curr = psnrHistory[i];
        var cpX = (toX(prev.iteration) + toX(curr.iteration)) / 2;
        ctx.quadraticCurveTo(cpX, toY(prev.psnr), toX(curr.iteration), toY(curr.psnr));
    }
    ctx.stroke();

    // Draw current value dot at latest point
    var latest = psnrHistory[psnrHistory.length - 1];
    var dotX = toX(latest.iteration);
    var dotY = toY(latest.psnr);

    // Outer glow ring
    ctx.fillStyle = 'rgba(0, 170, 255, 0.3)';
    ctx.beginPath();
    ctx.arc(dotX, dotY, 8, 0, Math.PI * 2);
    ctx.fill();

    // Inner dot
    ctx.fillStyle = '#00aaff';
    ctx.beginPath();
    ctx.arc(dotX, dotY, 4, 0, Math.PI * 2);
    ctx.fill();

    // White center
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(dotX, dotY, 1.5, 0, Math.PI * 2);
    ctx.fill();

    // Value label near the dot
    ctx.fillStyle = '#e0e0e0';
    ctx.font = 'bold 11px JetBrains Mono, monospace';
    ctx.textAlign = 'left';

    // Position label to the left if dot is too far right
    var labelText = latest.psnr.toFixed(1) + ' dB';
    var labelX = dotX + 10;
    var labelY = dotY - 8;
    var labelWidth = ctx.measureText(labelText).width;

    if (labelX + labelWidth > W - padding.right) {
        labelX = dotX - labelWidth - 10;
    }
    if (labelY < padding.top + 12) {
        labelY = dotY + 16;
    }

    // Draw label background for readability
    ctx.fillStyle = 'rgba(26, 26, 46, 0.85)';
    ctx.fillRect(labelX - 4, labelY - 11, labelWidth + 8, 16);

    ctx.fillStyle = '#e0e0e0';
    ctx.fillText(labelText, labelX, labelY);
}

// ── 3D Viewer ───────────────────────────────────────────────

function showViewer(url) {
    var container = document.getElementById('viewer-container');
    var iframe = document.getElementById('viewer-iframe');
    if (container.hidden) {
        container.hidden = false;
        iframe.src = url;
    }
}

// ── Pipeline Completion ─────────────────────────────────────

function onPipelineComplete() {
    // Close SSE
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }

    // Stop stage timer
    if (stageTimerInterval) {
        clearInterval(stageTimerInterval);
        stageTimerInterval = null;
    }

    // Mark all stages as complete
    document.querySelectorAll('.stage-item').forEach(function (item) {
        item.classList.remove('active', 'error');
        item.classList.add('complete');
        item.querySelector('.stage-icon').innerHTML = '<span class="checkmark">\u2713</span>';
    });

    // Update progress to 100%
    updateOverallProgress(1.0);
    document.getElementById('progress-stage-label').textContent = 'Complete!';
    document.getElementById('progress-message').textContent = 'Gaussian splat generated successfully';

    // Show download buttons
    var dlSection = document.getElementById('download-section');
    dlSection.hidden = false;
    document.getElementById('download-ply').href = '/api/download/' + currentJobId + '/splat.ply';
    document.getElementById('download-ksplat').href = '/api/download/' + currentJobId + '/terrain.ksplat';

    // Reset buttons
    document.getElementById('cancel-btn').hidden = true;
    document.getElementById('start-btn').hidden = false;
    document.getElementById('start-btn').disabled = true;  // can't restart same job
    document.getElementById('preset-select').disabled = false;

    appendLog('--- Pipeline complete ---');
}

function onPipelineEnd() {
    // Generic cleanup for cancel/error
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    if (stageTimerInterval) {
        clearInterval(stageTimerInterval);
        stageTimerInterval = null;
    }
    document.getElementById('cancel-btn').hidden = true;
    document.getElementById('start-btn').hidden = false;
    document.getElementById('start-btn').disabled = false;  // allow retry
    document.getElementById('preset-select').disabled = false;
}

// ── Error Display ───────────────────────────────────────────

function showError(message) {
    // Remove existing error banner if present
    var existing = document.querySelector('.error-banner');
    if (existing) existing.remove();

    var banner = document.createElement('div');
    banner.className = 'error-banner';
    banner.textContent = message;

    // Add a close button
    var closeBtn = document.createElement('span');
    closeBtn.textContent = ' \u00d7';
    closeBtn.style.cursor = 'pointer';
    closeBtn.style.fontWeight = 'bold';
    closeBtn.style.float = 'right';
    closeBtn.style.marginLeft = '12px';
    closeBtn.style.fontSize = '1.2rem';
    closeBtn.addEventListener('click', function () {
        banner.remove();
    });
    banner.prepend(closeBtn);

    var progressPanel = document.querySelector('.progress-panel');
    if (progressPanel) {
        progressPanel.prepend(banner);
    } else {
        // Fallback: prepend to main content
        var main = document.querySelector('.main-content');
        if (main) main.prepend(banner);
    }

    // Mark current stage as error
    if (currentStage) {
        var item = document.querySelector('.stage-item[data-stage="' + currentStage + '"]');
        if (item) {
            item.classList.remove('active');
            item.classList.add('error');
            item.querySelector('.stage-icon').innerHTML = '<span class="error-icon">\u2717</span>';
        }
    }

    appendLog('[ERROR] ' + message);
}

// ── Log Output ──────────────────────────────────────────────

function appendLog(message) {
    var log = document.getElementById('log-output');
    if (!log) return;
    var timestamp = new Date().toLocaleTimeString();
    log.textContent += '[' + timestamp + '] ' + message + '\n';
    log.scrollTop = log.scrollHeight;
}

// ── HTML Escape Helper ──────────────────────────────────────

function escapeHtml(str) {
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
}
