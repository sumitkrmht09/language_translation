// App State
const state = {
    languages: {},          // Raw language mapping from server
    selectedLangs: new Set(), // Selected language codes
    xliffFiles: [],         // Array of File objects
    graphicsFiles: [],      // Array of File objects
    activeJobId: null,
    eventSource: null
};

// DOM Elements
const elements = {
    form: document.getElementById('translation-form'),
    xlfInput: document.getElementById('xlf_files'),
    zipInput: document.getElementById('zip_files'),
    xlfDropzone: document.getElementById('xliff-dropzone'),
    zipDropzone: document.getElementById('graphics-dropzone'),
    xlfFileList: document.getElementById('xliff-file-list'),
    zipFileList: document.getElementById('graphics-file-list'),
    languagesContainer: document.getElementById('languages-container'),
    langSearch: document.getElementById('lang-search'),
    btnSelectAll: document.getElementById('btn-select-all'),
    btnClearAll: document.getElementById('btn-clear-all'),
    btnSubmit: document.getElementById('btn-submit'),
    
    // Progress Panel
    progressPanel: document.getElementById('progress-panel'),
    jobStatusBadge: document.getElementById('job-status-badge'),
    progressBarFill: document.getElementById('job-progress-bar'),
    progressPercent: document.getElementById('job-progress-percent'),
    jobEta: document.getElementById('job-eta'),
    languagesStatusList: document.getElementById('languages-status-list'),
    consoleLogs: document.getElementById('console-logs'),
    btnClearConsole: document.getElementById('btn-clear-console'),
    
    // Downloads Panel
    downloadsPanel: document.getElementById('downloads-panel'),
    downloadCardsContainer: document.getElementById('download-cards-container')
};

// Initial Setup
document.addEventListener('DOMContentLoaded', () => {
    fetchLanguages();
    setupDropzone(elements.xlfDropzone, elements.xlfInput, 'xliff');
    setupDropzone(elements.zipDropzone, elements.zipInput, 'graphics');
    setupEventListeners();
});

// Event Listeners Configuration
function setupEventListeners() {
    // Search Languages
    elements.langSearch.addEventListener('input', filterLanguages);
    
    // Select/Clear All Languages
    elements.btnSelectAll.addEventListener('click', () => toggleAllLanguages(true));
    elements.btnClearAll.addEventListener('click', () => toggleAllLanguages(false));
    
    // Form Submit
    elements.btnSubmit.addEventListener('click', startTranslationJob);
    
    // Clear Console
    elements.btnClearConsole.addEventListener('click', () => {
        elements.consoleLogs.innerHTML = '';
        logToConsole('Console cleared.', 'system');
    });
}

// Fetch languages from backend API
async function fetchLanguages() {
    try {
        const response = await fetch('/api/languages');
        if (!response.ok) throw new Error('Failed to load languages');
        state.languages = await response.json();
        renderLanguages();
    } catch (err) {
        console.error(err);
        elements.languagesContainer.innerHTML = `
            <div class="lang-loader" style="color: var(--color-danger)">
                Error loading languages. Please check if the server is running and reload.
            </div>
        `;
    }
}

// Render languages checkboxes inside panel
function renderLanguages() {
    elements.languagesContainer.innerHTML = '';
    
    Object.entries(state.languages).forEach(([code, label]) => {
        const item = document.createElement('label');
        item.className = 'lang-checkbox-label';
        item.dataset.langCode = code;
        item.dataset.langName = label.toLowerCase();
        
        item.innerHTML = `
            <div class="lang-info">
                <span class="lang-code-badge">${code}</span>
                <span class="lang-name">${label}</span>
            </div>
            <input type="checkbox" value="${code}">
        `;
        
        const checkbox = item.querySelector('input');
        checkbox.addEventListener('change', (e) => {
            if (e.target.checked) {
                state.selectedLangs.add(code);
                item.classList.add('checked');
            } else {
                state.selectedLangs.delete(code);
                item.classList.remove('checked');
            }
            validateForm();
        });
        
        elements.languagesContainer.appendChild(item);
    });
}

// Filter languages based on search keyword
function filterLanguages(e) {
    const query = e.target.value.toLowerCase().trim();
    const items = elements.languagesContainer.querySelectorAll('.lang-checkbox-label');
    
    items.forEach(item => {
        const code = item.dataset.langCode.toLowerCase();
        const name = item.dataset.langName;
        
        if (code.includes(query) || name.includes(query)) {
            item.style.display = 'flex';
        } else {
            item.style.display = 'none';
        }
    });
}

// Select All / Clear All
function toggleAllLanguages(check) {
    const checkboxes = elements.languagesContainer.querySelectorAll('input[type="checkbox"]');
    checkboxes.forEach(cb => {
        // Skip hidden ones when filtering
        const label = cb.closest('.lang-checkbox-label');
        if (label.style.display === 'none') return;
        
        cb.checked = check;
        const code = cb.value;
        if (check) {
            state.selectedLangs.add(code);
            label.classList.add('checked');
        } else {
            state.selectedLangs.delete(code);
            label.classList.remove('checked');
        }
    });
    validateForm();
}

// Dropzone configuration helper
function setupDropzone(dropzone, input, type) {
    // Intercept clicks on dropzone links
    dropzone.addEventListener('click', (e) => {
        if (e.target.closest('.file-list') || e.target.closest('.btn-remove')) {
            return; // Don't trigger input dialog if clicking delete button
        }
        input.click();
    });
    
    input.addEventListener('change', () => {
        handleFileSelection(input.files, type);
        input.value = ''; // Reset input element so same files can be re-selected
    });
    
    // Drag and drop support
    ['dragenter', 'dragover'].forEach(eventName => {
        dropzone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.add('dragover');
        }, false);
    });
    
    ['dragleave', 'drop'].forEach(eventName => {
        dropzone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropzone.classList.remove('dragover');
        }, false);
    });
    
    dropzone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        handleFileSelection(dt.files, type);
    }, false);
}

// Process selected files, validate extensions, update lists
function handleFileSelection(files, type) {
    Array.from(files).forEach(file => {
        const ext = file.name.split('.').pop().toLowerCase();
        
        if (type === 'xliff') {
            if (ext !== 'xlf' && ext !== 'xliff') {
                alert(`Invalid format: ${file.name} is not an XLIFF file.`);
                return;
            }
            // Check if already in list
            if (state.xliffFiles.some(f => f.name === file.name && f.size === file.size)) return;
            state.xliffFiles.push(file);
        } else {
            if (ext !== 'zip') {
                alert(`Invalid format: ${file.name} is not a ZIP file.`);
                return;
            }
            if (state.graphicsFiles.some(f => f.name === file.name && f.size === file.size)) return;
            state.graphicsFiles.push(file);
        }
    });
    
    renderFileList(type);
    validateForm();
}

// Format byte size
function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

// Render files list under dropzone
function renderFileList(type) {
    const listElement = type === 'xliff' ? elements.xlfFileList : elements.zipFileList;
    const filesArray = type === 'xliff' ? state.xliffFiles : state.graphicsFiles;
    
    listElement.innerHTML = '';
    
    filesArray.forEach((file, index) => {
        const item = document.createElement('div');
        item.className = 'file-item';
        
        item.innerHTML = `
            <span class="file-name" title="${file.name}">${file.name}</span>
            <span class="file-size">${formatBytes(file.size)}</span>
            <button type="button" class="btn-remove" title="Remove file">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="18" y1="6" x2="6" y2="18"></line>
                    <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
            </button>
        `;
        
        item.querySelector('.btn-remove').addEventListener('click', (e) => {
            e.stopPropagation();
            filesArray.splice(index, 1);
            renderFileList(type);
            validateForm();
        });
        
        listElement.appendChild(item);
    });
}

// Validate form states before enabling translation submit button
function validateForm() {
    const hasXliff = state.xliffFiles.length > 0;
    const hasLangs = state.selectedLangs.size > 0;
    
    elements.btnSubmit.disabled = !(hasXliff && hasLangs);
}

// Submit translation job
async function startTranslationJob() {
    if (state.xliffFiles.length === 0 || state.selectedLangs.size === 0) return;
    
    elements.btnSubmit.disabled = true;
    elements.btnSubmit.querySelector('span').textContent = 'Uploading...';
    
    const formData = new FormData();
    
    // Add XLIFF files
    state.xliffFiles.forEach(file => {
        formData.append('xlf_files', file);
    });
    
    // Add ZIP files
    state.graphicsFiles.forEach(file => {
        formData.append('zip_files', file);
    });
    
    // Add comma-separated languages list
    formData.append('languages', Array.from(state.selectedLangs).join(','));
    
    // Add concurrent workers
    const selectedWorkers = elements.form.querySelector('input[name="max_workers"]:checked').value;
    formData.append('max_workers', selectedWorkers);
    
    try {
        const response = await fetch('/api/translate', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || 'Failed to submit translation job');
        }
        
        const result = await response.json();
        setupProgressView(result.job_id);
        
    } catch (err) {
        alert(err.message);
        validateForm();
        elements.btnSubmit.querySelector('span').textContent = 'Translate & Process';
    }
}

// Clear panels, show progress screen and bind SSE stream
function setupProgressView(jobId) {
    state.activeJobId = jobId;
    
    // Hide downloads, clear cards
    elements.downloadsPanel.classList.add('hidden');
    elements.downloadCardsContainer.innerHTML = '';
    
    // Reset Progress Bar
    elements.progressBarFill.style.width = '0%';
    elements.progressPercent.textContent = '0%';
    elements.jobStatusBadge.className = 'status-badge';
    elements.jobStatusBadge.textContent = 'Processing';
    elements.jobEta.textContent = 'ETA: Calculating...';
    
    // Clear & setup individual languages list
    elements.languagesStatusList.innerHTML = '';
    Array.from(state.selectedLangs).forEach(lang => {
        const label = state.languages[lang] || lang;
        const chip = document.createElement('div');
        chip.className = 'lang-status-chip pending';
        chip.id = `lang-status-${lang}`;
        chip.innerHTML = `
            <span class="lang-status-dot"></span>
            <span>${label}</span>
        `;
        elements.languagesStatusList.appendChild(chip);
    });
    
    // Clear console & print initiation message
    elements.consoleLogs.innerHTML = '';
    logToConsole(`Job initialized: ${jobId}`, 'system');
    logToConsole(`Connecting to progress broadcast channel...`, 'system');
    
    // Display Progress Panel
    elements.progressPanel.classList.remove('hidden');
    elements.progressPanel.scrollIntoView({ behavior: 'smooth' });
    
    // Start SSE Listening
    connectToProgressBroadcast(jobId);
}

// Print lines in console
function logToConsole(message, type = 'info') {
    const line = document.createElement('div');
    line.className = `log-line ${type}`;
    
    // Prepend timestamp
    const now = new Date();
    const timeStr = now.toTimeString().split(' ')[0];
    line.textContent = `[${timeStr}] ${message}`;
    
    elements.consoleLogs.appendChild(line);
    elements.consoleLogs.scrollTop = elements.consoleLogs.scrollHeight;
}

// SSE Connection Handler
function connectToProgressBroadcast(jobId) {
    if (state.eventSource) {
        state.eventSource.close();
    }
    
    const sseUrl = `/api/jobs/${jobId}/progress`;
    state.eventSource = new EventSource(sseUrl);
    
    // 1. Progress Stream updates
    state.eventSource.addEventListener('progress', (e) => {
        try {
            const data = JSON.parse(e.data);
            
            // Update UI elements
            const pct = Math.round(data.progress * 100);
            elements.progressBarFill.style.width = `${pct}%`;
            elements.progressPercent.textContent = `${pct}%`;
            
            if (data.eta_seconds !== undefined) {
                if (data.eta_seconds > 0) {
                    const mins = Math.floor(data.eta_seconds / 60);
                    const secs = data.eta_seconds % 60;
                    elements.jobEta.textContent = `ETA: ${mins}m ${secs}s`;
                } else {
                    elements.jobEta.textContent = 'ETA: Wrapping up...';
                }
            }
            
            if (data.message) {
                logToConsole(data.message, 'info');
            }
            
            // Mark individual languages status
            if (data.languages_done) {
                data.languages_done.forEach(lang => {
                    const chip = document.getElementById(`lang-status-${lang}`);
                    if (chip) {
                        chip.className = 'lang-status-chip completed';
                    }
                });
            }
            
            // Highlight active language
            if (data.detail && data.detail.lang) {
                const activeLang = data.detail.lang;
                const chip = document.getElementById(`lang-status-${activeLang}`);
                if (chip && !chip.classList.contains('completed')) {
                    chip.className = 'lang-status-chip processing';
                }
                if (data.detail.step_message) {
                    logToConsole(`[${activeLang}] ${data.detail.step_message}`, 'info');
                }
            }
            
        } catch (err) {
            console.error('Failed to parse progress event:', err);
        }
    });
    
    // 2. Job complete broadcast
    state.eventSource.addEventListener('complete', (e) => {
        try {
            const data = JSON.parse(e.data);
            
            elements.progressBarFill.style.width = '100%';
            elements.progressPercent.textContent = '100%';
            elements.jobEta.textContent = 'Completed!';
            
            elements.jobStatusBadge.className = 'status-badge completed';
            elements.jobStatusBadge.textContent = 'Completed';
            
            logToConsole(data.message || 'Translation job complete!', 'success');
            
            // Make all chips completed
            Array.from(state.selectedLangs).forEach(lang => {
                const chip = document.getElementById(`lang-status-${lang}`);
                if (chip) {
                    chip.className = 'lang-status-chip completed';
                }
            });
            
            // Render downloads
            renderDownloads(data.downloads);
            
            // Shutdown SSE
            state.eventSource.close();
            validateForm();
            elements.btnSubmit.querySelector('span').textContent = 'Translate & Process';
            
        } catch (err) {
            console.error('Failed to parse complete event:', err);
            state.eventSource.close();
        }
    });
    
    // 3. Job failure / error broadcast
    state.eventSource.addEventListener('error', (e) => {
        // EventSource triggers error events on connection issues, but we also push manual "error" channel events
        if (e.data) {
            try {
                const data = JSON.parse(e.data);
                elements.jobStatusBadge.className = 'status-badge failed';
                elements.jobStatusBadge.textContent = 'Failed';
                elements.jobEta.textContent = 'Error occurred';
                logToConsole(`Error: ${data.message || 'Unknown server error'}`, 'error');
            } catch (err) {
                logToConsole('Connection interrupted. Retrying...', 'warn');
                return; // Let EventSource auto-retry connection
            }
        } else {
            // General connection error
            logToConsole('Connection to server lost. Reconnecting...', 'warn');
            return;
        }
        
        state.eventSource.close();
        validateForm();
        elements.btnSubmit.querySelector('span').textContent = 'Translate & Process';
    });
}

// Render downloads cards when job completes
function renderDownloads(downloads) {
    elements.downloadCardsContainer.innerHTML = '';
    
    if (!downloads || downloads.length === 0) {
        elements.downloadCardsContainer.innerHTML = `
            <div class="lang-loader" style="grid-column: 1/-1;">
                No files were generated during the job. Check the console for logs.
            </div>
        `;
    } else {
        downloads.forEach(dl => {
            const card = document.createElement('div');
            card.className = 'download-card';
            
            card.innerHTML = `
                <div class="dl-details">
                    <div class="dl-icon-wrap">
                        <svg class="dl-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                            <polyline points="7 10 12 15 17 10"></polyline>
                            <line x1="12" y1="15" x2="12" y2="3"></line>
                        </svg>
                    </div>
                    <div class="dl-info">
                        <span class="dl-name" title="${dl.name}">${dl.name}</span>
                        <span class="dl-meta">ZIP Deliverable</span>
                    </div>
                </div>
                <a href="${dl.url}" class="btn-download" title="Download ZIP file" download>
                    <svg class="btn-download-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                        <polyline points="7 10 12 15 17 10"></polyline>
                        <line x1="12" y1="15" x2="12" y2="3"></line>
                    </svg>
                </a>
            `;
            
            elements.downloadCardsContainer.appendChild(card);
        });
    }
    
    elements.downloadsPanel.classList.remove('hidden');
    elements.downloadsPanel.scrollIntoView({ behavior: 'smooth' });
}
