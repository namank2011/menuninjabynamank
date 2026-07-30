// Intercept all API fetch calls to use our live backend URL when hosted on Vercel
(function () {
    const originalFetch = window.fetch;
    const API_BASE = "https://menuninjabynamank.onrender.com";
    window.fetch = function (url, options) {
        options = options || {};
        options.headers = options.headers || {};

        // Attach token from localStorage as fallback for third-party cookie restrictions
        const token = localStorage.getItem('session_token');
        if (token) {
            options.headers['Authorization'] = `Bearer ${token}`;
            options.headers['X-Session-Token'] = token;
        }

        // Cross-origin request detection for cookies (API credentials inclusion)
        const isVercel = window.location.hostname.endsWith('vercel.app') || window.location.hostname.includes('vercel');
        if (isVercel) {
            options.credentials = 'include';
            if (typeof url === 'string' && url.startsWith('/api')) {
                url = API_BASE + url;
            }
        }
        return originalFetch(url, options);
    };
})();

// Menu Ninja Menu Digitizer Frontend SPA State
let currentView = 'dashboard';
let currentDraftId = null;
let currentDraft = null; // Holds the active draft data { id, businessName, defaults, files, items, status }
let uploadedFiles = [];
let templateFile = null;
let currentStep = 1;
let selectedItemId = null; // Item ID currently selected for compare pane

document.addEventListener('DOMContentLoaded', () => {
    // Initial load
    initUploadEvents();
    loadSavedSettings();
    checkAuthentication();
});

// Switch major views: dashboard, create, review-flow
function switchView(viewName) {
    currentView = viewName;
    document.querySelectorAll('.content-view').forEach(v => v.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));

    document.getElementById(`view-${viewName}`).classList.add('active');

    if (viewName === 'dashboard') {
        document.getElementById('btn-dash').classList.add('active');
        document.getElementById('save-draft-btn').style.display = 'none';
        document.getElementById('audit-log-toggle').style.display = 'none';
        currentDraftId = null;
        currentDraft = null;
        loadDraftsList();
    } else if (viewName === 'create') {
        document.getElementById('btn-create').classList.add('active');
        document.getElementById('save-draft-btn').style.display = 'none';
        document.getElementById('audit-log-toggle').style.display = 'none';
        clearUploadForm();
    } else if (viewName === 'users') {
        const usersBtn = document.getElementById('btn-users');
        if (usersBtn) usersBtn.classList.add('active');
        document.getElementById('save-draft-btn').style.display = 'none';
        document.getElementById('audit-log-toggle').style.display = 'none';
        loadUsersList();
    } else if (viewName === 'review-flow') {
        document.getElementById('save-draft-btn').style.display = 'inline-flex';
        document.getElementById('audit-log-toggle').style.display = 'inline-flex';
    }
}

// Clear state when clicking New Extraction
function clearUploadForm() {
    uploadedFiles = [];
    templateFile = null;
    document.getElementById('uploaded-files-list').innerHTML = '';
    document.getElementById('form-business-name').value = '';
    document.getElementById('chk-use-default-template').checked = true;
    document.getElementById('template-upload-zone').style.display = 'none';
    document.getElementById('template-file-input').value = '';
    updateExtractionBtnState();
}

// ----------------- DRAG & DROP UPLOAD -----------------

function initUploadEvents() {
    const dropZone = document.getElementById('file-drop-zone');
    const fileInput = document.getElementById('menu-file-input');

    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('active');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('active');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('active');
        handleFileSelect(e.dataTransfer.files);
    });

    fileInput.addEventListener('change', (e) => {
        handleFileSelect(e.target.files);
    });

    // Custom template input
    document.getElementById('template-file-input').addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            templateFile = e.target.files[0];
        } else {
            templateFile = null;
        }
        updateExtractionBtnState();
    });
}

function handleFileSelect(files) {
    for (let file of files) {
        uploadedFiles.push(file);
    }
    renderUploadedFilesList();
    updateExtractionBtnState();
}

function renderUploadedFilesList() {
    const container = document.getElementById('uploaded-files-list');
    container.innerHTML = '';

    uploadedFiles.forEach((file, index) => {
        const row = document.createElement('div');
        row.className = 'file-row';

        let typeIcon = 'fa-file-lines';
        if (file.type.startsWith('image/')) typeIcon = 'fa-file-image';
        else if (file.name.endsWith('.pdf')) typeIcon = 'fa-file-pdf';
        else if (file.name.endsWith('.xlsx') || file.name.endsWith('.xls')) typeIcon = 'fa-file-excel';
        else if (file.name.endsWith('.docx') || file.name.endsWith('.doc')) typeIcon = 'fa-file-word';

        row.innerHTML = `
            <div class="file-info">
                <i class="fa-solid ${typeIcon}"></i>
                <div>
                    <span class="file-name">${file.name}</span>
                    <span class="file-size">${(file.size / 1024).toFixed(1)} KB</span>
                </div>
            </div>
            <button class="delete-file-btn" onclick="removeUploadedFile(${index})">
                <i class="fa-solid fa-trash-can"></i>
            </button>
        `;
        container.appendChild(row);
    });
}

function removeUploadedFile(index) {
    uploadedFiles.splice(index, 1);
    renderUploadedFilesList();
    updateExtractionBtnState();
}

function toggleTemplateUpload(chk) {
    const zone = document.getElementById('template-upload-zone');
    if (chk.checked) {
        zone.style.display = 'none';
        templateFile = null;
    } else {
        zone.style.display = 'block';
    }
    updateExtractionBtnState();
}

function updateExtractionBtnState() {
    const businessName = document.getElementById('form-business-name').value.trim();
    const useDefault = document.getElementById('chk-use-default-template').checked;
    const btn = document.getElementById('start-extraction-btn');
    const quickBtn = document.getElementById('quick-export-btn');

    let disabled = !(businessName && uploadedFiles.length > 0);
    if (!useDefault && !templateFile) {
        disabled = true;
    }

    if (btn) btn.disabled = disabled;
    if (quickBtn) quickBtn.disabled = disabled;
}

// Auto enable buttons when typing in form
document.getElementById('form-business-name').addEventListener('input', updateExtractionBtnState);

// ----------------- FETCHING AND TRIGGERING FLOWS -----------------

async function triggerExtraction(directApprove = false) {
    const btn = directApprove ? document.getElementById('quick-export-btn') : document.getElementById('start-extraction-btn');
    const originalHtml = btn.innerHTML;

    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Processing... Please Wait`;
    btn.disabled = true;

    const otherBtn = directApprove ? document.getElementById('start-extraction-btn') : document.getElementById('quick-export-btn');
    if (otherBtn) otherBtn.disabled = true;

    try {
        const formData = new FormData();
        formData.append('business_name', document.getElementById('form-business-name').value.trim());
        uploadedFiles.forEach(file => {
            formData.append('menu_files', file);
        });

        const useDefault = document.getElementById('chk-use-default-template').checked;
        formData.append('default_template', useDefault);
        if (!useDefault && templateFile) {
            formData.append('template_file', templateFile);
        }

        // defaults
        formData.append('tax_category', document.getElementById('form-tax-category').value);
        formData.append('tax_type', document.getElementById('form-tax-type').value);
        formData.append('tax_value', parseFloat(document.getElementById('form-tax-value').value) || 0);
        formData.append('master_status', document.getElementById('form-master-status').value);
        formData.append('menu_status', 'Active');
        formData.append('stock_status', 'Active');
        formData.append('station', document.getElementById('form-station').value.trim() || 'Kitchen');
        formData.append('preparation_time', document.getElementById('form-prep-time').value.trim());
        formData.append('default_dietary', document.getElementById('form-dietary').value);
        formData.append('direct_approve', directApprove);
        formData.append('extraction_engine', document.getElementById('form-extraction-engine').value);

        const headers = {};
        const apiKey = localStorage.getItem("gemini_api_key");
        if (apiKey) {
            headers['X-Gemini-API-Key'] = apiKey;
        }
        const response = await fetch('/api/drafts', {
            method: 'POST',
            headers: headers,
            body: formData
        });

        const res = await response.json();
        if (response.ok && res.draftId) {
            if (directApprove) {
                currentDraftId = res.draftId;

                // Show review flow
                switchView('review-flow');

                // Reset steps / wrappers to display success page directly
                document.querySelector('.stepper-progress').style.display = 'none';
                document.querySelector('.review-flow-footer').style.display = 'none';
                document.querySelector('.review-sidebar-column').style.display = 'none';

                document.querySelectorAll('.stage-contents').forEach(el => el.classList.remove('active'));
                const stage6 = document.getElementById('stage-panel-6');
                stage6.classList.add('active');

                document.querySelector('.approval-agreement-card').style.display = 'none';
                document.querySelector('.validation-summary-card').style.display = 'none';

                const card = document.getElementById('success-export-card');
                card.style.display = 'flex';

                const excelLink = document.getElementById('link-excel-download');
                excelLink.href = res.downloadOutputUrl;
                excelLink.setAttribute('download', res.outputFile || 'bulk_upload.xlsx');

                const reportLink = document.getElementById('link-report-download');
                reportLink.href = res.downloadReviewReportTxtUrl;
                reportLink.setAttribute('download', res.downloadReviewReportTxtUrl.split('/').pop());

                const jsonLink = document.getElementById('link-json-download');
                jsonLink.href = res.downloadReviewReportJsonUrl;
                jsonLink.setAttribute('download', res.downloadReviewReportJsonUrl.split('/').pop());

                // Programmatic download trigger
                const link = document.createElement('a');
                link.href = res.downloadOutputUrl;
                link.setAttribute('download', res.outputFile || 'bulk_upload.xlsx');
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);

                showToast('Success! Menu was extracted, validated against template instructions, formatted, and downloaded directly.', 'success');
            } else {
                // Traditional review flow
                document.querySelector('.stepper-progress').style.display = 'flex';
                document.querySelector('.review-flow-footer').style.display = 'flex';
                document.querySelector('.review-sidebar-column').style.display = 'block';
                document.querySelector('.approval-agreement-card').style.display = 'block';
                document.querySelector('.validation-summary-card').style.display = 'block';

                showToast('Menu extraction complete! Entering Stage 1 Raw Review.', 'success');
                await loadDraft(res.draftId);
            }
        } else {
            showToast('Extraction failed: ' + (res.error || 'Unknown error'), 'error');
        }
    } catch (e) {
        showToast('Request failed: ' + e.message, 'error');
    } finally {
        btn.innerHTML = originalHtml;
        updateExtractionBtnState();
    }
}

async function loadDraft(draftId) {
    currentDraftId = draftId;
    switchView('review-flow');

    // Reset wizard displays if coming from direct export success layout
    document.querySelector('.stepper-progress').style.display = 'flex';
    document.querySelector('.review-flow-footer').style.display = 'flex';
    document.querySelector('.review-sidebar-column').style.display = 'block';
    document.querySelector('.approval-agreement-card').style.display = 'block';
    document.querySelector('.validation-summary-card').style.display = 'block';
    document.getElementById('success-export-card').style.display = 'none';

    // Enable active review stepper step buttons
    document.querySelectorAll('.step-nav-btn').forEach(btn => btn.disabled = false);

    try {
        const headers = {};
        const apiKey = localStorage.getItem("gemini_api_key");
        if (apiKey) {
            headers['X-Gemini-API-Key'] = apiKey;
        }
        const r = await fetch(`/api/drafts/${draftId}`, {
            headers: headers
        });
        if (r.ok) {
            currentDraft = await r.json();

            // Set up processing telemetry banner
            const telemetryBanner = document.getElementById('extraction-telemetry-banner');
            const telemetryText = document.getElementById('telemetry-text');
            if (telemetryBanner && telemetryText && currentDraft && currentDraft.files && currentDraft.files.length > 0) {
                let infoStrings = [];
                currentDraft.files.forEach(f => {
                    if (f.engine && f.timeSeconds !== undefined) {
                        let engLabel = f.engine;
                        if (f.engine === 'auto') engLabel = 'Auto Detect';
                        else if (f.engine === 'gemini') engLabel = 'Gemini Cloud';
                        else if (f.engine === 'ollama') engLabel = 'Ollama Local';
                        else if (f.engine === 'heuristics') engLabel = 'Heuristics (Offline)';

                        infoStrings.push(`${f.name} (${f.timeSeconds}s via ${engLabel})`);
                    } else {
                        infoStrings.push(`${f.name}`);
                    }
                });
                if (infoStrings.length > 0) {
                    telemetryText.innerHTML = `<strong>Processing Telemetry:</strong> ${infoStrings.join(', ')}`;
                    telemetryBanner.style.display = 'flex';
                } else {
                    telemetryBanner.style.display = 'none';
                }
            } else if (telemetryBanner) {
                telemetryBanner.style.display = 'none';
            }

            goToStep(1); // Start from Stage 1
        } else {
            showToast('Failed loading draft detail.', 'error');
            switchView('dashboard');
        }
    } catch (e) {
        showToast('Network error loading draft: ' + e.message, 'error');
    }
}

async function loadDraftsList() {
    try {
        const r = await fetch('/api/drafts');
        const list = await r.json();
        const tbody = document.getElementById('drafts-list-body');
        if (!tbody) return;

        if (!Array.isArray(list)) {
            console.error("Failed loading drafts list (not an array):", list);
            tbody.innerHTML = `<tr><td colspan="5" class="empty-state error-text">Session expired or unauthorized. Please sign in again.</td></tr>`;
            return;
        }

        if (list.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" class="empty-state">No drafts found. Try creating a new menu digitization.</td></tr>`;
            return;
        }

        tbody.innerHTML = '';
        list.forEach(d => {
            const tr = document.createElement('tr');

            const badgeClass = d.status === 'Approved' ? 'status-badge reviewed' : 'status-badge not-reviewed';
            const actionBtn = d.status === 'Approved'
                ? `<button class="secondary-btn btn-sm" onclick="loadDraft('${d.id}')"><i class="fa-solid fa-eye"></i> View</button>`
                : `<button class="primary-btn btn-sm" onclick="loadDraft('${d.id}')"><i class="fa-solid fa-pencil"></i> Resume</button>`;

            // Build subtitle with engines/times
            let subTexts = [];
            if (d.files && Array.isArray(d.files)) {
                d.files.forEach(f => {
                    if (f.engine && f.timeSeconds !== undefined) {
                        let shortEng = f.engine;
                        if (f.engine === 'heuristics') shortEng = 'Heuristics';
                        else if (f.engine === 'gemini') shortEng = 'Gemini';
                        else if (f.engine === 'ollama') shortEng = 'Ollama';
                        else if (f.engine === 'auto') shortEng = 'Auto';
                        subTexts.push(`${shortEng} (${f.timeSeconds}s)`);
                    }
                });
            }
            const subTitleHtml = subTexts.length > 0 ? `<div style="font-size:11px; color:#888888; font-weight:normal; margin-top:4px;"><i class="fa-solid fa-gauge-high"></i> ${subTexts.join(', ')}</div>` : '';

            tr.innerHTML = `
                <td><strong>${d.businessName}</strong>${subTitleHtml}</td>
                <td>${formatIsoDate(d.createdAt)}</td>
                <td>${formatIsoDate(d.updatedAt)}</td>
                <td><span class="${badgeClass}">${d.status}</span></td>
                <td class="actions-col">
                    <div style="display:flex; gap:6px;">
                        ${actionBtn}
                        <button class="secondary-btn danger-btn btn-sm" onclick="deleteDraftApi('${d.id}')"><i class="fa-solid fa-trash"></i></button>
                    </div>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed loading drafts: ', e);
    }
}

async function deleteDraftApi(id) {
    if (confirm('Are you absolutely sure you want to delete this menu draft? This action is irreversible.')) {
        await fetch(`/api/drafts/${id}`, { method: 'DELETE' });
        loadDraftsList();
    }
}

// ----------------- STEPPER NAVIGATION -----------------

function goToStep(stepNum) {
    currentStep = stepNum;

    // Manage stepper bar
    document.querySelectorAll('.step-indicator').forEach(ind => {
        const step = parseInt(ind.getAttribute('data-step'));
        ind.classList.remove('current', 'completed');

        if (step === currentStep) {
            ind.classList.add('current');
        } else if (step < currentStep) {
            ind.classList.add('completed');
        }
    });

    document.querySelectorAll('.step-nav-btn').forEach(btn => {
        const step = parseInt(btn.getAttribute('data-step'));
        btn.classList.remove('active', 'completed');
        if (step === currentStep) {
            btn.classList.add('active');
        } else if (step < currentStep) {
            btn.classList.add('completed');
        }
    });

    // Toggle panels
    document.querySelectorAll('.stage-contents').forEach(p => p.classList.remove('active'));

    // Map simplified 3 steps to active panels Group
    let panelId = 1;
    if (stepNum === 1) panelId = 1;
    else if (stepNum === 2) panelId = 5;
    else if (stepNum === 3) panelId = 6;

    const panelEl = document.getElementById(`stage-panel-${panelId}`);
    if (panelEl) panelEl.classList.add('active');

    // Adjust title bar
    const titles = {
        1: "Step 1: Verification Spreadsheet & Category Clean",
        2: "Step 2: Live Digital Menu Preview",
        3: "Step 3: Final Validation Certificate & Excel Generation"
    };
    document.getElementById('workspace-title').textContent = titles[currentStep] || "Menu Digitizer Review";
    document.getElementById('workspace-subtitle').textContent = `Business: ${currentDraft.businessName} (Step ${currentStep} of 3)`;

    // Redraw stepper-progress-line
    const pct = ((currentStep - 1) / 2) * 100;
    document.getElementById('stepper-progress-line').style.width = `${pct}%`;

    // Render contents based on stage
    if (currentStep === 1) {
        renderTableStage1();
        renderCategoryReview();
    } else if (currentStep === 2) {
        renderMenuPreview();
    } else if (currentStep === 3) {
        renderFinalApproval();
    }

    // Toggle previous/next buttons
    document.getElementById('wizard-prev-btn').disabled = (currentStep === 1);

    const nextBtn = document.getElementById('wizard-next-btn');
    if (currentStep === 3) {
        nextBtn.style.display = 'none';
    } else {
        nextBtn.style.display = 'inline-flex';
    }

    // Toggle sticky columns spacing
    if (currentStep === 1) {
        document.getElementById('review-sidebar-column').style.display = 'block';
        document.getElementById('review-main-column').style.gridColumn = 'span 1';
    } else {
        document.getElementById('review-sidebar-column').style.display = 'none';
        document.getElementById('review-main-column').style.gridColumn = 'span 2';
    }

    updateFooterSummary();
}

function nextStep() {
    if (currentStep < 3) goToStep(currentStep + 1);
}

function prevStep() {
    if (currentStep > 1) goToStep(currentStep - 1);
}

function updateFooterSummary() {
    // Count invalid items
    let invalidCount = 0;
    let pendingReview = 0;

    currentDraft.items.forEach(it => {
        const blocking = it.validationErrors ? it.validationErrors.filter(e => e.type === 'Blocking Error') : [];
        if (blocking.length > 0) {
            invalidCount++;
        }
        if (!it.approved) {
            pendingReview++;
        }
    });

    const statusDiv = document.getElementById('wizard-summary-status');
    if (statusDiv) {
        statusDiv.innerHTML = `
            Draft status: <strong class="warning-text">${invalidCount} details block export</strong> | 
            Unapproved items: <strong>${pendingReview} total</strong>
        `;
    }

    // Update red review required badge inside navigation bar
    const badge = document.getElementById('flagged-count-badge');
    if (badge) {
        badge.textContent = invalidCount;
        badge.style.display = invalidCount > 0 ? 'inline-block' : 'none';
    }
}

// ----------------- VIEW 1: RAW EXTRACTION TABLE -----------------

function renderTableStage1() {
    const tbody = document.getElementById('stage1-table-body');
    if (!tbody) return;
    if (!currentDraft || !currentDraft.items) return;

    const searchInput = document.getElementById('stage1-search');
    const searchVal = searchInput ? searchInput.value.toLowerCase() : '';
    const catInput = document.getElementById('stage1-filter-cat');
    const catVal = catInput ? (catInput.value || 'all') : 'all';
    const confInput = document.getElementById('stage1-filter-conf');
    const confVal = confInput ? confInput.value : 'all';

    // Filter list
    const filtered = currentDraft.items.filter(it => {
        const matchesSearch = it.productName.toLowerCase().includes(searchVal) ||
            it.categoryName.toLowerCase().includes(searchVal) ||
            it.description.toLowerCase().includes(searchVal);

        const matchesCat = (catVal === 'all') || (it.categoryName === catVal);

        let matchesConf = true;
        if (confVal === 'low') matchesConf = (it.confidence !== null && it.confidence < 0.75);
        else if (confVal === 'high') matchesConf = (it.confidence === null || it.confidence >= 0.75);

        return matchesSearch && matchesCat && matchesConf;
    });

    // Populate category values dropdown inside toolbar once
    const catFilter = document.getElementById('stage1-filter-cat');
    if (catFilter.children.length <= 1) {
        const uniqueCats = Array.from(new Set(currentDraft.items.map(it => it.categoryName))).sort();
        catFilter.innerHTML = '<option value="all">All Categories</option>';
        uniqueCats.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c;
            opt.textContent = c;
            catFilter.appendChild(opt);
        });
        catFilter.value = catVal;
    }

    tbody.innerHTML = '';

    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="12" class="empty-state">No matching menu products.</td></tr>`;
        return;
    }

    filtered.forEach(it => {
        const tr = document.createElement('tr');
        if (it.id === selectedItemId) tr.classList.add('selected');

        tr.onclick = () => selectItemForCompare(it);

        // Group variation inputs helper functions
        const variations = it.variations || [];
        const varStr = variations.map(v => v.name).join('#');
        const spStr = variations.map(v => v.sellingPrice !== null ? v.sellingPrice : '').join('#');
        const lpStr = variations.map(v => v.listingPrice !== null ? v.listingPrice : '').join('#');

        // Review badge
        let badgeHtml = '';
        if (it.approved) {
            badgeHtml = `<span class="status-badge reviewed" onclick="toggleItemApproval('${it.id}')">Approved</span>`;
        } else if (it.reviewStatus === 'Blocked') {
            badgeHtml = `<span class="status-badge blocked" onclick="toggleItemApproval('${it.id}')">Blocked</span>`;
        } else if (it.reviewStatus === 'Review Required') {
            badgeHtml = `<span class="status-badge review-required" onclick="toggleItemApproval('${it.id}')">Flagged</span>`;
        } else {
            badgeHtml = `<span class="status-badge not-reviewed" onclick="toggleItemApproval('${it.id}')">Pending</span>`;
        }

        // Tax cells validation status indicator
        const isMasterErr = hasErrorOnField(it, 'masterStatus') ? 'col-error' : '';
        const isCatErr = hasErrorOnField(it, 'categoryName') ? 'col-error' : '';
        const isNameErr = hasErrorOnField(it, 'productName') ? 'col-error' : '';
        const isSpErr = hasErrorOnField(it, 'sellingPrice') || hasErrorOnField(it, 'variations') ? 'col-error' : '';
        const isLpErr = hasErrorOnField(it, 'listingPrice') ? 'col-error' : '';

        tr.innerHTML = `
            <td>${badgeHtml}</td>
            <td class="${isCatErr}">
                <input type="text" value="${escapeHtml(it.categoryName)}" onchange="updateLocalItem('${it.id}', 'categoryName', this.value)">
            </td>
            <td class="${isNameErr}">
                <input type="text" value="${escapeHtml(it.productName)}" onchange="updateLocalItem('${it.id}', 'productName', this.value)">
            </td>
            <td>
                <input type="text" value="${escapeHtml(it.variantGroupName)}" onchange="updateLocalItem('${it.id}', 'variantGroupName', this.value)" placeholder="e.g. Size">
            </td>
            <td>
                <input type="text" value="${escapeHtml(varStr)}" onchange="updateVariationsFromString('${it.id}', 'name', this.value)" placeholder="Small#Medium">
            </td>
            <td class="${isSpErr}">
                <input type="text" value="${escapeHtml(spStr)}" onchange="updateVariationsFromString('${it.id}', 'sellingPrice', this.value)" placeholder="99#149">
            </td>
            <td class="${isLpErr}">
                <input type="text" value="${escapeHtml(lpStr)}" onchange="updateVariationsFromString('${it.id}', 'listingPrice', this.value)" placeholder="120#170">
            </td>
            <td>
                <input type="text" value="${escapeHtml(it.description)}" onchange="updateLocalItem('${it.id}', 'description', this.value)" placeholder="Delicious item details...">
            </td>
            <td>
                <select onchange="updateLocalItem('${it.id}', 'dietaryTag', this.value)">
                    <option value="" ${it.dietaryTag === '' ? 'selected' : ''}>Blank</option>
                    <option value="veg" ${it.dietaryTag === 'veg' || it.dietaryTag === 'Veg' ? 'selected' : ''}>Veg</option>
                    <option value="non veg" ${it.dietaryTag === 'non veg' || it.dietaryTag === 'Non-Veg' ? 'selected' : ''}>Non-Veg</option>
                    <option value="egg" ${it.dietaryTag === 'egg' || it.dietaryTag === 'Egg' ? 'selected' : ''}>Egg</option>
                </select>
            </td>
            <td class="narrow-col">${it.confidence ? (it.confidence * 100).toFixed(0) + '%' : '-'}</td>
            <td class="narrow-col">${it.validationErrors && it.validationErrors.length > 0 ? `<i class="fa-solid fa-circle-exclamation warning-text" title="${it.validationErrors[0].message}"></i>` : `<i class="fa-solid fa-circle-check" style="color:var(--success)"></i>`}</td>
            <td class="actions-col">
                <button class="action-icon-btn delete-icon" onclick="deleteLocalItem('${it.id}')" title="Delete Product"><i class="fa-solid fa-trash-can"></i></button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

function hasErrorOnField(item, field) {
    if (!item.validationErrors) return false;
    return item.validationErrors.some(e => e.field.startsWith(field));
}

// ----------------- LIVE CLIENT VALIDATION SYNC -----------------

function updateLocalItem(itemId, field, value) {
    const item = currentDraft.items.find(it => it.id === itemId);
    if (!item) return;

    item[field] = value;

    // Immediate recalculate validations
    revalidateLocalMenu();
    renderTableStage1();
    updateFooterSummary();
    if (selectedItemId === itemId) {
        selectItemForCompare(item);
    }
}

function updateVariationsFromString(itemId, subfield, rawString) {
    const item = currentDraft.items.find(it => it.id === itemId);
    if (!item) return;

    const parts = rawString.split('#').map(p => p.trim());

    // Restructure variations
    const maxLen = Math.max(item.variations.length, parts.length);
    let newVars = [];

    for (let i = 0; i < maxLen; i++) {
        let v = item.variations[i] || { name: '', sellingPrice: null, listingPrice: null, confidence: 1 };

        let pVal = parts[i] !== undefined ? parts[i] : '';
        if (pVal === '' && i >= parts.length) {
            // Trim if index is outside bounds
            continue;
        }

        if (subfield === 'name') {
            v.name = pVal;
        } else if (subfield === 'sellingPrice') {
            v.sellingPrice = pVal === '' ? null : pVal;
        } else if (subfield === 'listingPrice') {
            v.listingPrice = pVal === '' ? null : pVal;
        }
        newVars.push(v);
    }

    // Safeguard singular inputs
    if (newVars.length === 0) {
        newVars = [{ name: '', sellingPrice: null, listingPrice: null, confidence: 1 }];
    }

    item.variations = newVars;
    revalidateLocalMenu();
    renderTableStage1();
    updateFooterSummary();
    if (selectedItemId === itemId) {
        selectItemForCompare(item);
    }
}

function addNewItemRow() {
    const newId = 'temp_' + Math.random().toString(36).substr(2, 9);
    const defaults = currentDraft.defaults;

    const newItem = {
        id: newId,
        source: {
            fileName: 'Manual Input',
            page: 1,
            rawText: 'Item row added manually by keyboard',
            confidence: 1.0
        },
        categoryName: 'Uncategorized',
        productName: 'New Product',
        variantGroupName: '',
        variations: [{ name: '', sellingPrice: null, listingPrice: null, confidence: 1.0 }],
        description: '',
        dietaryTag: defaults.dietaryTag || '',
        masterStatus: defaults.masterStatus || 'Active',
        menuStatus: 'Active',
        stockStatus: 'Active',
        itemCode: '',
        station: defaults.station || 'Kitchen',
        preparationTime: defaults.preparationTime || '',
        imageUrl1: '',
        imageUrl2: '',
        imageUrl3: '',
        taxCategory: defaults.taxCategory || 'Services',
        taxType: defaults.taxType || 'GST',
        taxValue: defaults.taxValue || 5.0,
        reviewStatus: 'Not Reviewed',
        approved: false
    };

    currentDraft.items.unshift(newItem);
    revalidateLocalMenu();
    renderTableStage1();
    updateFooterSummary();
    selectItemForCompare(newItem);
}

function deleteLocalItem(itemId) {
    if (confirm('Delete this product?')) {
        currentDraft.items = currentDraft.items.filter(it => it.id !== itemId);
        revalidateLocalMenu();
        renderTableStage1();
        updateFooterSummary();
    }
}

function toggleItemApproval(itemId) {
    const item = currentDraft.items.find(it => it.id === itemId);
    if (!item) return;

    item.approved = !item.approved;
    revalidateLocalMenu();
    renderTableStage1();
    updateFooterSummary();
}

function bulkApproveItems() {
    // Approve all items currently displayed in grid stage 1 that do not possess blocking errors
    let approvedCount = 0;
    currentDraft.items.forEach(it => {
        const blocking = it.validationErrors ? it.validationErrors.filter(e => e.type === 'Blocking Error') : [];
        if (blocking.length === 0) {
            it.approved = true;
            approvedCount++;
        }
    });
    showToast(`Bulk approved ${approvedCount} products (skipped items with blocking errors).`, 'success');
    revalidateLocalMenu();
    renderTableStage1();
    updateFooterSummary();
}

// ----------------- STAGE 2: FLAGGED RECORD RESOLUTION -----------------

function renderTableStage2() {
    const tbody = document.getElementById('stage2-table-body');
    if (!tbody) return;
    if (!currentDraft || !currentDraft.items) return;
    tbody.innerHTML = '';

    // Filter items with errors/warnings
    const flagged = currentDraft.items.filter(it => {
        return it.validationErrors && it.validationErrors.length > 0;
    });

    if (flagged.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-state"><i class="fa-solid fa-circle-check" style="color:var(--success); font-size: 28px; margin-bottom:12px; display:block;"></i> All products clear! No validation flags detected. Click Next Step to continue.</td></tr>`;
        return;
    }

    flagged.forEach(it => {
        const tr = document.createElement('tr');
        tr.onclick = () => selectItemForCompare(it);

        const variations = it.variations || [];
        const varStr = variations.map(v => `${v.name} (Selling: ${v.sellingPrice || '-'}, Listing: ${v.listingPrice || '-'})`).join('<br>');

        let errorsListHtml = it.validationErrors.map(e => {
            const labelClass = e.type === 'Blocking Error' ? 'badge-err blocking' : 'badge-err warning';
            return `<div style="margin-bottom: 6px;"><span class="${labelClass}">${e.type}</span>: ${e.message}</div>`;
        }).join('');

        // Action selector or direct input for stage 2
        tr.innerHTML = `
            <td><span class="status-badge blocked">Flagged</span></td>
            <td>
                <input type="text" value="${escapeHtml(it.categoryName)}" onchange="updateLocalItem('${it.id}', 'categoryName', this.value)">
            </td>
            <td>
                <input type="text" value="${escapeHtml(it.productName)}" onchange="updateLocalItem('${it.id}', 'productName', this.value)">
            </td>
            <td style="font-size:12px; color:var(--text-secondary);">${varStr}</td>
            <td>${errorsListHtml}</td>
            <td>
                <button class="primary-btn btn-sm" onclick="quickFixModal('${it.id}')"><i class="fa-solid fa-wrench"></i> Fix Item</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

function quickFixModal(itemId) {
    // Navigate user's visual focus to Stage 1 grid to clean the specific field highlighted in Red
    goToStep(1);
    const searchInput = document.getElementById('stage1-search');
    const item = currentDraft.items.find(it => it.id === itemId);
    if (item) {
        searchInput.value = item.productName;
        renderTableStage1();
        selectItemForCompare(item);
    }
}

// ----------------- STAGE 3: CATEGORY REVIEW & MERGING -----------------

function renderCategoryReview() {
    const container = document.getElementById('category-cards-list');
    if (!container) return;
    if (!currentDraft || !currentDraft.items) return;

    const sourceSelect = document.getElementById('merge-source');
    const targetSelect = document.getElementById('merge-target');

    // Calculate item counts per category
    const catCounts = {};
    currentDraft.items.forEach(it => {
        const cat = it.categoryName || 'Uncategorized';
        catCounts[cat] = (catCounts[cat] || 0) + 1;
    });

    container.innerHTML = '';
    sourceSelect.innerHTML = '<option value="">-- Select Source Category --</option>';
    targetSelect.innerHTML = '<option value="">-- Select Target Category --</option>';

    const sortedCats = Object.keys(catCounts).sort();

    sortedCats.forEach(cat => {
        // Render category card
        const card = document.createElement('div');
        card.className = 'category-card';
        card.innerHTML = `
            <div class="category-title">
                <i class="fa-solid fa-folder-open" style="color:var(--primary)"></i>
                <input type="text" class="category-name-input" value="${escapeHtml(cat)}" onchange="renameCategoryBulk('${escapeJs(cat)}', this.value)">
            </div>
            <span class="item-count-badge">${catCounts[cat]} products</span>
        `;
        container.appendChild(card);

        // Options for merging dropdowns
        const optSrc = document.createElement('option');
        optSrc.value = cat;
        optSrc.textContent = `${cat} (${catCounts[cat]} items)`;
        sourceSelect.appendChild(optSrc);

        const optTgt = document.createElement('option');
        optTgt.value = cat;
        optTgt.textContent = `${cat} (${catCounts[cat]} items)`;
        targetSelect.appendChild(optTgt);
    });
}

function renameCategoryBulk(oldName, newName) {
    oldName = oldName.trim();
    newName = newName.trim();
    if (!newName || oldName === newName) return;

    let count = 0;
    currentDraft.items.forEach(it => {
        if (it.categoryName === oldName) {
            it.categoryName = newName;
            count++;
        }
    });

    revalidateLocalMenu();
    renderCategoryReview();
    updateFooterSummary();
    alert(`Bulk renamed category for ${count} items.`);
}

function executeCategoryMerge() {
    const src = document.getElementById('merge-source').value;
    const tgt = document.getElementById('merge-target').value;

    if (!src || !tgt) {
        alert('Please specify both source and target categories.');
        return;
    }

    if (src === tgt) {
        alert('Source and target categories must be different.');
        return;
    }

    if (confirm(`Are you sure you want to merge all items in '${src}' into '${tgt}'? This will reassign category titles.`)) {
        let count = 0;
        currentDraft.items.forEach(it => {
            if (it.categoryName === src) {
                it.categoryName = tgt;
                count++;
            }
        });

        revalidateLocalMenu();
        renderCategoryReview();
        updateFooterSummary();
        alert(`Successfully merged. Reassigned ${count} items.`);
    }
}

// ----------------- STAGE 4: PRICE & VARIATIONS REVIEW -----------------

function renderVariationsReview() {
    const container = document.getElementById('variations-grid-list');
    if (!container) return;
    if (!currentDraft || !currentDraft.items) return;
    container.innerHTML = '';

    // Filter products containing variations
    const varItems = currentDraft.items.filter(it => {
        return it.variations && (
            it.variations.length > 1 ||
            (it.variations.length === 1 && it.variations[0].name !== '')
        );
    });

    if (varItems.length === 0) {
        container.innerHTML = `<div class="empty-state" style="grid-column:span 2;"><i class="fa-solid fa-circle-info" style="font-size:24px; margin-bottom:8px; display:block;"></i> No variation items in this menu. Everything is flat, single-priced products.</div>`;
        return;
    }

    varItems.forEach(it => {
        const card = document.createElement('div');
        card.className = 'var-product-card';

        const rowsHtml = it.variations.map((v, i) => `
            <tr>
                <td><strong>${escapeHtml(v.name)}</strong></td>
                <td>₹${v.sellingPrice || '-'}</td>
                <td>₹${v.listingPrice || v.sellingPrice || '-'}</td>
            </tr>
        `).join('');

        const finalVarStr = it.variations.map(v => v.name).join('#');
        const finalSpStr = it.variations.map(v => v.sellingPrice || '').join('#');
        const finalLpStr = it.variations.map(v => v.listingPrice || v.sellingPrice || '').join('#');

        card.innerHTML = `
            <div class="var-card-header">
                <h4>${escapeHtml(it.productName)}</h4>
                <span>Group: ${escapeHtml(it.variantGroupName || 'Size')}</span>
            </div>
            <table class="var-grid-table">
                <thead>
                    <tr>
                        <th>Variation Name</th>
                        <th>Selling Price</th>
                        <th>Listing Price (MRP)</th>
                    </tr>
                </thead>
                <tbody>
                    ${rowsHtml}
                </tbody>
            </table>
            <div class="var-representation-preview">
                <span class="rep-item"><strong>Variation:</strong> ${escapeHtml(finalVarStr)}</span>
                <span class="rep-item"><strong>Selling Price*:</strong> ${escapeHtml(finalSpStr)}</span>
                <span class="rep-item"><strong>Listing Price:</strong> ${escapeHtml(finalLpStr)}</span>
            </div>
        `;
        container.appendChild(card);
    });
}

// ----------------- STAGE 5: MENU preview -----------------

function renderMenuPreview() {
    const container = document.getElementById('menu-preview-container');
    if (!container) return;
    if (!currentDraft || !currentDraft.items) return;
    document.getElementById('preview-menu-business').textContent = currentDraft.businessName;

    // Group approved items by category
    const catMap = {};
    const approved = currentDraft.items.filter(it => it.approved);

    if (approved.length === 0) {
        container.innerHTML = `<div class="empty-state"><i class="fa-solid fa-circle-exclamation" style="font-size:24px; margin-bottom:8px; display:block;"></i> No approved items. Mark products as 'Approved' in Stage 1 to preview them.</div>`;
        return;
    }

    approved.forEach(it => {
        const cat = it.categoryName || 'Uncategorized';
        if (!catMap[cat]) catMap[cat] = [];
        catMap[cat].push(it);
    });

    container.innerHTML = '';

    const sortedCats = Object.keys(catMap).sort();
    sortedCats.forEach(cat => {
        const sec = document.createElement('div');
        sec.className = 'preview-category-section';

        const itemsListHtml = catMap[cat].map(it => {
            const variations = it.variations || [];
            let priceHtml = '';
            let varsHtml = '';

            if (variations.length > 1 || (variations.length === 1 && variations[0].name !== '')) {
                priceHtml = variations.map(v => `₹${v.sellingPrice}`).join(' / ');
                varsHtml = `<span class="menu-item-vars">Sizes: ${variations.map(v => `${v.name}`).join(', ')}</span>`;
            } else if (variations.length === 1) {
                priceHtml = `₹${variations[0].sellingPrice || '0'}`;
            }

            let dietClass = 'veg';
            if (it.dietaryTag === 'Non-Veg') dietClass = 'non-veg';
            else if (it.dietaryTag === 'Egg') dietClass = 'egg';

            const dietIndicator = it.dietaryTag
                ? `<span class="diet-tag-indicator ${dietClass}">${it.dietaryTag}</span>`
                : '';

            return `
                <div class="menu-item-row">
                    <div class="menu-item-top">
                        <span class="item-name-block">${escapeHtml(it.productName)} ${dietIndicator}</span>
                        <div class="dot-leader"></div>
                        <span class="menu-item-price">${priceHtml}</span>
                    </div>
                    ${it.description ? `<p class="menu-item-desc">${escapeHtml(it.description)}</p>` : ''}
                    ${varsHtml}
                </div>
            `;
        }).join('');

        sec.innerHTML = `
            <h3>${escapeHtml(cat)}</h3>
            <div class="preview-items-list">
                ${itemsListHtml}
            </div>
        `;
        container.appendChild(sec);
    });
}

// ----------------- STAGE 6: FINAL EXPORT -----------------

function renderFinalApproval() {
    const list = document.getElementById('final-validation-checklist');
    if (!list) return;
    if (!currentDraft || !currentDraft.items) return;
    list.innerHTML = '';

    let totalCount = currentDraft.items.length;
    let approvedCount = currentDraft.items.filter(it => it.approved).length;
    let blockedCount = currentDraft.items.filter(it => it.reviewStatus === 'Blocked').length;
    let warningCount = currentDraft.items.filter(it => it.reviewStatus === 'Review Required').length;

    // Checks
    // 1. Mandatory Fields
    const checkMandatory = blockedCount === 0;
    addChecklistItem(list, checkMandatory, "Mandatory Fields: Category, Name, and Prices verified for exporting products", blockedCount > 0 ? `fail` : `pass`, `${blockedCount} blocking errors found`);

    // 2. Variations sequence matching
    let badVars = 0;
    currentDraft.items.filter(it => it.approved).forEach(it => {
        const vNames = it.variations.map(v => v.name);
        const vPrices = it.variations.map(v => v.sellingPrice);
        if (vNames.length !== vPrices.length) {
            badVars++;
        }
    });

    addChecklistItem(list, badVars === 0, "Variation Mapping: All variations align index-to-index with selling prices", badVars > 0 ? `fail` : `pass`, badVars > 0 ? `${badVars} sequence mismatches` : '');

    // 3. Document Approval Ratio
    const chkApprRatio = approvedCount > 0;
    addChecklistItem(list, chkApprRatio, `Approved products count: ${approvedCount} of ${totalCount} items approved`, approvedCount === 0 ? `warn` : `pass`, approvedCount === 0 ? 'No products will be exported' : '');

    // Reset agreement checked
    document.getElementById('chk-final-approval').checked = false;
    toggleFinalExportButton();
}

function addChecklistItem(ul, ok, message, statusType, note) {
    const li = document.createElement('li');
    li.className = statusType;

    let icon = '<i class="fa-solid fa-circle-check"></i>';
    if (statusType === 'fail') icon = '<i class="fa-solid fa-circle-xmark"></i>';
    else if (statusType === 'warn') icon = '<i class="fa-solid fa-circle-question"></i>';

    li.innerHTML = `
        ${icon}
        <div>
            <span>${message}</span>
            ${note ? `<small style="display:block; color:var(--text-secondary); font-size:11px; margin-top:2px;">[${note}]</small>` : ''}
        </div>
    `;
    ul.appendChild(li);
}

function toggleFinalExportButton() {
    const chk = document.getElementById('chk-final-approval');
    const btn = document.getElementById('final-export-xlsx-btn');

    // Check if there are blocking errors in approved items
    let hasBlockedAppr = currentDraft.items.some(it => it.approved && it.reviewStatus === 'Blocked');
    let hasApprovedItems = currentDraft.items.some(it => it.approved);

    if (chk.checked && !hasBlockedAppr && hasApprovedItems) {
        btn.disabled = false;
    } else {
        btn.disabled = true;
    }
}

async function approveAndDownloadExcel() {
    const btn = document.getElementById('final-export-xlsx-btn');
    btn.disabled = true;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Finalizing menu for export...`;

    // Save changes first
    await saveDraftProgress(true);

    try {
        const headers = { 'Content-Type': 'application/json' };
        const apiKey = localStorage.getItem("gemini_api_key");
        if (apiKey) {
            headers['X-Gemini-API-Key'] = apiKey;
        }
        const response = await fetch(`/api/drafts/${currentDraftId}/approve`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ approvedAgreement: true })
        });

        const res = await response.json();

        if (response.ok) {
            // Success export card
            document.querySelector('.approval-agreement-card').style.display = 'none';
            document.querySelector('.validation-summary-card').style.display = 'none';

            const card = document.getElementById('success-export-card');
            card.style.display = 'flex';

            const excelLink = document.getElementById('link-excel-download');
            excelLink.href = res.downloadOutputUrl;
            excelLink.setAttribute('download', res.outputFile || 'bulk_upload.xlsx');

            const reportLink = document.getElementById('link-report-download');
            reportLink.href = res.downloadReviewReportTxtUrl;
            reportLink.setAttribute('download', res.reviewReportTxt || 'report.txt');

            const jsonLink = document.getElementById('link-json-download');
            jsonLink.href = res.downloadReviewReportJsonUrl;
            jsonLink.setAttribute('download', res.reviewReportJson || 'report.json');

            showToast('Excel file and review reports successfully generated! Select download links below.', 'success');
        } else {
            showToast('Approval failed: ' + (res.error || 'Unknown error'), 'error');
            btn.innerHTML = `<i class="fa-solid fa-file-excel"></i> Generate & Download Menu Ninja Excel File`;
            btn.disabled = false;
        }
    } catch (e) {
        showToast('Network request error: ' + e.message, 'error');
        btn.innerHTML = `<i class="fa-solid fa-file-excel"></i> Generate & Download Menu Ninja Excel File`;
        btn.disabled = false;
    }
}

// ----------------- SIDEBAR COMPARISON VIEWER -----------------

function selectItemForCompare(item) {
    selectedItemId = item.id;

    // Highlight table row
    document.querySelectorAll('.review-grid-table tr').forEach(r => r.classList.remove('selected'));

    // UI details
    document.getElementById('source-viewer-empty').style.display = 'none';
    const activeView = document.getElementById('source-viewer-active');
    activeView.style.display = 'flex';

    document.getElementById('source-view-name').textContent = item.productName || 'Unlabeled';
    document.getElementById('source-view-file').textContent = item.source.fileName || 'Unknown File';
    document.getElementById('source-view-confidence').textContent = `Confidence: ${(item.source.confidence * 100).toFixed(0)}%`;
    document.getElementById('source-view-raw-text').textContent = item.source.rawText || '(No raw extraction snippet is available)';

    // Update validation alerts and reasons
    const errorsList = document.getElementById('source-view-errors-list');
    const errorsGroup = document.getElementById('source-view-errors-group');
    const noErrorsGroup = document.getElementById('source-view-no-errors-group');

    if (item.validationErrors && item.validationErrors.length > 0) {
        errorsList.innerHTML = item.validationErrors.map(e => {
            const isBlocking = e.type === 'Blocking Error';
            const color = isBlocking ? '#e74c3c' : '#f39c12';
            const bg = isBlocking ? 'rgba(231, 76, 60, 0.15)' : 'rgba(243, 156, 18, 0.15)';
            return `<div style="margin-bottom: 8px; font-size: 12px; border-bottom: 1px solid rgba(0,0,0,0.05); padding-bottom: 8px;">
                <span style="background-color: ${color}; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: bold; text-transform: uppercase; margin-right: 6px; display: inline-block;">${e.type}</span>
                <span style="font-weight: 600; color: #2c3e50;">Field: ${e.field}</span>
                <div style="margin-top: 5px; color: #444; line-height: 1.4; font-weight: 500;">${e.message}</div>
            </div>`;
        }).join('');
        errorsGroup.style.display = 'block';
        noErrorsGroup.style.display = 'none';
    } else {
        errorsGroup.style.display = 'none';
        noErrorsGroup.style.display = 'block';
    }
}

async function generateSingleDescription() {
    if (!selectedItemId) return;

    const activeItem = currentDraft.items.find(it => it.id === selectedItemId);
    if (!activeItem) return;

    if (activeItem.description) {
        if (!confirm('This item already has a description. Do you want to overwrite it with AI generated description?')) {
            return;
        }
    }

    const triggerBtn = document.querySelector('#source-viewer-active button');
    triggerBtn.disabled = true;
    triggerBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Generating...`;

    try {
        const headers = { 'Content-Type': 'application/json' };
        const apiKey = localStorage.getItem("gemini_api_key");
        if (apiKey) {
            headers['X-Gemini-API-Key'] = apiKey;
        }
        const response = await fetch(`/api/drafts/${currentDraftId}/generate-descriptions`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ itemIds: [selectedItemId] })
        });

        const res = await response.json();

        if (response.ok && res.updated > 0) {
            // Reload local item
            const headers = {};
            const apiKey = localStorage.getItem("gemini_api_key");
            if (apiKey) {
                headers['X-Gemini-API-Key'] = apiKey;
            }
            const root_r = await fetch(`/api/drafts/${currentDraftId}`, {
                headers: headers
            });
            if (root_r.ok) {
                currentDraft = await root_r.json();
                const refreshedItem = currentDraft.items.find(it => it.id === selectedItemId);
                if (refreshedItem) {
                    selectItemForCompare(refreshedItem);
                }

                // Redraw table
                if (currentStep === 1) renderTableStage1();
                else if (currentStep === 2) renderTableStage2();
                updateFooterSummary();
            }
            showToast('AI description generated successfully!', 'success');
        } else {
            showToast('Generation failed. Make sure Ollama contains the text model llama3.1:latest and is online.', 'error');
        }
    } catch (e) {
        showToast('Description generator failed: ' + e.message, 'error');
    } finally {
        triggerBtn.disabled = false;
        triggerBtn.innerHTML = `<i class="fa-solid fa-robot"></i> Generate description`;
    }
}

// ----------------- CRUD SYNC -----------------

async function saveDraftProgress(silent = false) {
    if (!currentDraftId) return;

    try {
        const headers = { 'Content-Type': 'application/json' };
        const apiKey = localStorage.getItem("gemini_api_key");
        if (apiKey) {
            headers['X-Gemini-API-Key'] = apiKey;
        }
        const response = await fetch(`/api/drafts/${currentDraftId}`, {
            method: 'PUT',
            headers: headers,
            body: JSON.stringify({
                businessName: currentDraft.businessName,
                defaults: currentDraft.defaults,
                items: currentDraft.items
            })
        });

        if (response.ok) {
            // Fetch validated list
            const headers = {};
            const apiKey = localStorage.getItem("gemini_api_key");
            if (apiKey) {
                headers['X-Gemini-API-Key'] = apiKey;
            }
            const r = await fetch(`/api/drafts/${currentDraftId}`, {
                headers: headers
            });
            if (r.ok) {
                currentDraft = await r.json();
            }

            if (!silent) {
                showToast('Draft progress successfully stored in database!', 'success');
                // Redraw
                goToStep(currentStep);
            }
        } else {
            showToast('Failed saving progress draft.', 'error');
        }
    } catch (e) {
        showToast('Network saving error: ' + e.message, 'error');
    }
}

// ----------------- AUDIT MODAL -----------------

async function toggleAuditLogsModal() {
    const modal = document.getElementById('audit-logs-modal');

    if (modal.classList.contains('active')) {
        modal.classList.remove('active');
    } else {
        // Fetch audit logs
        try {
            const r = await fetch(`/api/drafts/${currentDraftId}/audit`);
            if (r.ok) {
                const logs = await r.json();
                const tbody = document.getElementById('audit-logs-table-body');
                tbody.innerHTML = '';

                if (logs.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="4" class="empty-state">No logs recorded yet.</td></tr>`;
                } else {
                    logs.forEach(log => {
                        const tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td><small>${formatIsoDate(log.timestamp)}</small></td>
                            <td><strong>${log.user}</strong></td>
                            <td><span class="status-badge reviewed">${log.action}</span></td>
                            <td>${log.details}</td>
                        `;
                        tbody.appendChild(tr);
                    });
                }
                modal.classList.add('active');
            }
        } catch (e) {
            showToast('Failed loading audit logs.', 'error');
        }
    }
}

// ----------------- LOCAL VALIDATION IMPLEMENTATION -----------------

// Helper lists
const ALLERGENS = ["Gluten", "Crustacean", "Egg", "Fish", "Nuts", "Peanut", "Soyabeans", "Milk", "Sulphite"];
const PORTION_UNITS = ["grams", "kg", "inches", "litre", "ml", "ounces", "pounds", "serves", "slices", "cms", "piece", "scoop"];

function revalidateLocalMenu() {
    currentDraft.items.forEach(item => {
        const errors = [];

        const productName = String(item.productName || '').trim();
        if (!productName) {
            errors.push({ type: "Blocking Error", field: "productName", message: "Product Name is required." });
        }

        const category = String(item.categoryName || '').trim();
        if (!category || category.toLowerCase() === 'uncategorized') {
            errors.push({ type: "Warning", field: "categoryName", message: "Product is uncategorized." });
        }

        const variations = item.variations || [];
        const variantGroup = String(item.variantGroupName || '').trim();

        if (variations.length === 0) {
            errors.push({ type: "Blocking Error", field: "variations", message: "At least one price/variation is required." });
        } else if (variations.length === 1 && !variations[0].name) {
            const price = variations[0].sellingPrice;
            if (price === null || String(price).trim() === '') {
                errors.push({ type: "Blocking Error", field: "sellingPrice", message: "Selling Price is required." });
            } else {
                const fPrice = parseFloat(price);
                if (isNaN(fPrice) || fPrice < 0) {
                    errors.push({ type: "Blocking Error", field: "sellingPrice", message: "Price must be a valid positive number." });
                }
            }
            const lp = variations[0].listingPrice;
            if (lp !== null && String(lp).trim() !== '') {
                const fLp = parseFloat(lp);
                const fSp = parseFloat(price || 0);
                if (isNaN(fLp) || fLp < fSp) {
                    errors.push({ type: "Blocking Error", field: "listingPrice", message: "Listing Price (MRP) cannot be less than Selling Price." });
                }
            }
        } else {
            if (!variantGroup) {
                errors.push({ type: "Warning", field: "variantGroupName", message: "Multiple variations exist but Variant Group is blank." });
            }
            variations.forEach((v, index) => {
                if (!String(v.name || '').trim()) {
                    errors.push({ type: "Blocking Error", field: `variations[${index}].name`, message: `Variation ${index + 1} name cannot be empty.` });
                }
                const sp = v.sellingPrice;
                if (sp === null || String(sp).trim() === '') {
                    errors.push({ type: "Blocking Error", field: `variations[${index}].sellingPrice`, message: `Selling price for variation '${v.name}' is required.` });
                } else {
                    const fSp = parseFloat(sp);
                    if (isNaN(fSp) || fSp < 0) {
                        errors.push({ type: "Blocking Error", field: `variations[${index}].sellingPrice`, message: "Price must be a valid positive number." });
                    }
                }
                const lp = v.listingPrice;
                if (lp !== null && String(lp).trim() !== '') {
                    const fLp = parseFloat(lp);
                    const fSp = parseFloat(sp || 0);
                    if (isNaN(fLp) || fLp < fSp) {
                        errors.push({ type: "Blocking Error", field: `variations[${index}].listingPrice`, message: "Listing Price (MRP) cannot be less than Selling Price." });
                    }
                }
            });
        }

        // Duplicate detector
        const normName = productName.toLowerCase().replace(/ /g, '');
        const normCat = category.toLowerCase().replace(/ /g, '');
        let isDup = false;

        currentDraft.items.forEach(other => {
            if (other.id === item.id) return;
            const oName = String(other.productName || '').trim().toLowerCase().replace(/ /g, '');
            const oCat = String(other.categoryName || '').trim().toLowerCase().replace(/ /g, '');
            if (oName === normName && oCat === normCat) {
                isDup = true;
            }
        });

        if (isDup) {
            errors.push({ type: "Warning", field: "productName", message: "Possible duplicate product detected in the same category." });
        }

        item.validationErrors = errors;

        // Sync local review status based on validators
        if (errors.some(e => e.type === 'Blocking Error')) {
            item.reviewStatus = 'Blocked';
        } else if (errors.some(e => e.type === 'Warning')) {
            item.reviewStatus = 'Review Required';
        } else {
            if (item.reviewStatus === 'Blocked' || item.reviewStatus === 'Review Required') {
                item.reviewStatus = 'Reviewed';
            }
        }
    });
}

// ----------------- HELPERS -----------------
function showToast(message, type = 'success') {
    const existing = document.getElementById('app-toast');
    if (existing) {
        existing.remove();
    }
    const toast = document.createElement('div');
    toast.id = 'app-toast';
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
        <i class="fa-solid ${type === 'success' ? 'fa-circle-check' : 'fa-circle-exclamation'}"></i>
        <span>${escapeHtml(message)}</span>
    `;
    document.body.appendChild(toast);
    setTimeout(() => { toast.classList.add('visible'); }, 10);
    setTimeout(() => {
        toast.classList.remove('visible');
        setTimeout(() => { toast.remove(); }, 300);
    }, 4000);
}

function toggleGuideBanner() {
    const content = document.getElementById('guide-banner-content');
    const text = document.getElementById('guide-toggle-text');
    const icon = document.getElementById('guide-toggle-icon');
    if (content.style.display === 'none') {
        content.style.display = 'block';
        text.innerText = 'Hide Guide';
        icon.className = 'fa-solid fa-chevron-up';
    } else {
        content.style.display = 'none';
        text.innerText = 'Show Guide';
        icon.className = 'fa-solid fa-chevron-down';
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function escapeJs(str) {
    if (!str) return '';
    return str.replace(/'/g, "\\'").replace(/"/g, '\\"');
}

function formatIsoDate(isoString) {
    if (!isoString) return '-';
    const date = new Date(isoString);
    return date.toLocaleString();
}

// ----------------- SETTINGS CONFIG -----------------
function loadSavedSettings() {
    const key = localStorage.getItem("gemini_api_key") || "";
    const input = document.getElementById("settings-gemini-key");
    if (input) {
        input.value = key;
    }
}

function toggleSettingsModal() {
    const modal = document.getElementById('settings-modal');
    if (modal.style.display === 'none') {
        loadSavedSettings();
        modal.style.display = 'flex';
    } else {
        modal.style.display = 'none';
    }
}

function saveSettings() {
    const key = document.getElementById("settings-gemini-key").value.trim();
    localStorage.setItem("gemini_api_key", key);
    showToast("Settings saved successfully!", 'success');
    toggleSettingsModal();
}

// ----------------- SECURITY & AUTHENTICATION FLOW -----------------
let currentUser = null;

async function checkAuthentication() {
    try {
        const [configRes, meRes] = await Promise.all([
            fetch('/api/auth/config').then(r => r.json()).catch(() => ({})),
            fetch('/api/auth/me')
        ]);

        const googleClientId = configRes ? configRes.google_client_id : "";
        if (meRes.ok) {
            currentUser = await meRes.json();
            onLoginSuccess(currentUser, googleClientId);
        } else {
            localStorage.removeItem('session_token');
            onLoginRequired(googleClientId);
        }
    } catch (e) {
        console.error("Auth check failed:", e);
        localStorage.removeItem('session_token');
        onLoginRequired("");
    }
}

function onLoginSuccess(user, googleClientId) {
    currentUser = user;

    document.getElementById('login-container').style.display = 'none';
    document.querySelector('.app-container').style.display = 'flex';

    document.getElementById('logged-in-email').textContent = user.email;
    document.getElementById('logged-in-email').title = user.email;

    const isAdmin = user.role === 'super_admin';

    // User Management nav button: admin only
    const usersBtn = document.getElementById('btn-users');
    if (usersBtn) usersBtn.style.display = isAdmin ? 'flex' : 'none';

    // Settings button: admin only
    const settingsBtn = document.getElementById('btn-settings');
    if (settingsBtn) settingsBtn.style.display = isAdmin ? 'flex' : 'none';

    loadDraftsList();
}

function onLoginRequired(googleClientId) {
    currentUser = null;
    document.getElementById('login-container').style.display = 'flex';
    document.querySelector('.app-container').style.display = 'none';

    initGoogleAuth(googleClientId);
}

function initGoogleAuth(clientId) {
    const section = document.getElementById('google-signin-section');
    const alertBox = document.getElementById('google-desc-alert');
    const loginCard = document.querySelector('.g_id_signin');

    if (!clientId) {
        if (alertBox) alertBox.style.display = 'block';
        if (loginCard) loginCard.style.display = 'none';
        return;
    }

    if (alertBox) alertBox.style.display = 'none';
    if (loginCard) loginCard.style.display = 'inline-block';

    try {
        google.accounts.id.initialize({
            client_id: clientId,
            callback: handleGoogleLoginResponse
        });
        google.accounts.id.renderButton(
            document.querySelector(".g_id_signin"),
            { theme: "outline", size: "large", width: "320" }
        );
        google.accounts.id.prompt();
    } catch (e) {
        console.error("Google auth init error:", e);
    }
}

async function handleGoogleLoginResponse(response) {
    const token = response.credential;
    const submitBtn = document.getElementById('btn-login-submit');
    submitBtn.disabled = true;
    const oldText = submitBtn.innerHTML;
    submitBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Google Sign In...`;

    try {
        const res = await fetch('/api/auth/google', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ credential: token })
        });

        if (res.ok) {
            const data = await res.json();
            if (data.token) {
                localStorage.setItem('session_token', data.token);
            }
            showToast('Google login successful!', 'success');
            onLoginSuccess(data.user);
        } else {
            const err = await res.json();
            showToast(err.error || 'Google login failed', 'error');
        }
    } catch (e) {
        showToast('System request failure: ' + e.message, 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = oldText;
    }
}

async function handleEmailLogin(event) {
    event.preventDefault();
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;
    const btn = document.getElementById('btn-login-submit');

    btn.disabled = true;
    const oldText = btn.innerHTML;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Authenticating...`;

    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ email, password })
        });

        if (res.ok) {
            const data = await res.json();
            if (data.token) {
                localStorage.setItem('session_token', data.token);
            }
            showToast('Welcome back, Ninja!', 'success');
            onLoginSuccess(data.user);
        } else {
            const err = await res.json();
            showToast(err.error || 'Invalid credentials or access deactivated', 'error');
        }
    } catch (e) {
        showToast('Login request error: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = oldText;
    }
}

async function handleLogout() {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
        localStorage.removeItem('session_token');
        showToast('You have signed out gracefully.', 'success');
        checkAuthentication();
    } catch (e) {
        showToast('Error during logout: ' + e.message, 'error');
    }
}

// ----------------- USER MANAGEMENT FLOW -----------------
function toggleAddUserModal() {
    const modal = document.getElementById('add-user-modal');
    modal.style.display = modal.style.display === 'none' || !modal.style.display ? 'flex' : 'none';
    if (modal.style.display === 'flex') {
        document.getElementById('add-user-form').reset();
        onRoleSelectChange();
    }
}

function openAddUserModal() {
    toggleAddUserModal();
}

function onRoleSelectChange() {
    const role = document.getElementById('new-user-role').value;
    const desc = document.getElementById('role-desc-text');
    if (desc) {
        if (role === 'super_admin') {
            desc.innerHTML = '<i class="fa-solid fa-circle-info" style="margin-right:4px;"></i> Super Administrators have full access: user management, settings, and all menus across the system.';
        } else {
            desc.innerHTML = '<i class="fa-solid fa-circle-info" style="margin-right:4px;"></i> Menu Operators can upload, extract, review and download their own menus only.';
        }
    }
}

async function handleAddUserSubmit(event) {
    event.preventDefault();
    const email = document.getElementById('new-user-email').value.trim();
    const role = document.getElementById('new-user-role').value;
    const password = document.getElementById('new-user-password').value;

    if (!password || password.length < 6) {
        showToast('Password is required and must be at least 6 characters.', 'error');
        return;
    }

    try {
        const res = await fetch('/api/users', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ email, role, password })
        });

        if (res.ok) {
            const roleName = role === 'super_admin' ? 'Super Administrator' : 'Menu Operator';
            showToast(`User created as ${roleName}!`, 'success');
            toggleAddUserModal();
            loadUsersList();
        } else {
            const err = await res.json();
            showToast(err.error || 'Failed to create user', 'error');
        }
    } catch (e) {
        showToast('Error creating user: ' + e.message, 'error');
    }
}

async function loadUsersList() {
    const tbody = document.getElementById('whitelist-users-body');
    if (!tbody) return;

    tbody.innerHTML = `<tr><td colspan="5" class="empty-state"><i class="fa-solid fa-spinner fa-spin"></i> Loading whitelist records...</td></tr>`;

    try {
        const res = await fetch('/api/users');
        if (res.ok) {
            const users = await res.json();
            if (users.length === 0) {
                tbody.innerHTML = `<tr><td colspan="5" class="empty-state">No users whitelisted yet.</td></tr>`;
                return;
            }
            tbody.innerHTML = users.map(u => {
                const label = u.role === 'super_admin'
                    ? '<span class="role-badge-admin"><i class="fa-solid fa-user-shield"></i> Super Admin</span>'
                    : '<span class="role-badge-reviewer"><i class="fa-solid fa-user"></i> Menu Operator</span>';
                const statusChecked = u.is_allowed ? 'checked' : '';
                const isSuperAdminEmail = u.email === 'namankshetri2@gmail.com';
                const toggleDisabled = isSuperAdminEmail ? 'disabled' : '';

                return `<tr class="whitelist-row">
                    <td class="whitelist-table-cell-bold">${escapeHtml(u.email)}</td>
                    <td class="whitelist-table-cell">${label}</td>
                    <td class="whitelist-table-cell-muted">${new Date(u.created_at).toLocaleString()}</td>
                    <td class="whitelist-table-cell">
                        <label class="whitelist-switch-label">
                            <input type="checkbox" class="whitelist-switch-input" ${statusChecked} ${toggleDisabled} onchange="toggleUserAccess('${u.email}', this.checked)">
                            <span class="whitelist-switch-slider"></span>
                        </label>
                    </td>
                    <td class="whitelist-table-cell-right">
                        <button onclick="deleteUserWhitelist('${u.email}')" class="secondary-btn btn-sm ${isSuperAdminEmail ? 'whitelist-btn-delete-hidden' : 'whitelist-btn-delete-visible'}">
                            <i class="fa-solid fa-trash"></i> Delete
                        </button>
                    </td>
                </tr>`;
            }).join('');
        } else {
            tbody.innerHTML = `<tr><td colspan="5" class="empty-state whitelist-error-state">Failed to load whitelisted users.</td></tr>`;
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty-state whitelist-error-state">Connection error: ${e.message}</td></tr>`;
    }
}

async function toggleUserAccess(email, isAllowed) {
    try {
        const res = await fetch(`/api/users/${encodeURIComponent(email)}/toggle`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ is_allowed: isAllowed })
        });
        if (res.ok) {
            showToast(`Access status updated for ${email}`, 'success');
        } else {
            const err = await res.json();
            showToast(err.error || 'Failed to update access', 'error');
            loadUsersList();
        }
    } catch (e) {
        showToast('Connection error: ' + e.message, 'error');
        loadUsersList();
    }
}

async function deleteUserWhitelist(email) {
    if (!confirm(`Are you sure you want to remove access and delete whitelisted email: ${email}?`)) {
        return;
    }
    try {
        const res = await fetch(`/api/users/${encodeURIComponent(email)}`, {
            method: 'DELETE'
        });
        if (res.ok) {
            showToast(`Successfully deleted ${email}`, 'success');
            loadUsersList();
        } else {
            const err = await res.json();
            showToast(err.error || 'Failed to delete user', 'error');
        }
    } catch (e) {
        showToast('Connection error: ' + e.message, 'error');
    }
}

function escapeHtml(text) {
    if (!text) return '';
    return text.toString()
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Bind to window context mapping for template/inline HTML call accessibility
window.checkAuthentication = checkAuthentication;
window.handleGoogleLoginResponse = handleGoogleLoginResponse;
window.handleEmailLogin = handleEmailLogin;
window.handleLogout = handleLogout;
window.toggleAddUserModal = toggleAddUserModal;
window.openAddUserModal = openAddUserModal;
window.handleAddUserSubmit = handleAddUserSubmit;
window.toggleUserAccess = toggleUserAccess;
window.deleteUserWhitelist = deleteUserWhitelist;
window.onRoleSelectChange = onRoleSelectChange;

