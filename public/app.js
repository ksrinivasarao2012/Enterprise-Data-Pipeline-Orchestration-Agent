// API Base Endpoint Path
const API_BASE = '/api';

// State parameters
let selectedIncidentId = null;

// Reusable Toast Notification System
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    
    // Create toast element
    const toast = document.createElement('div');
    toast.className = `glass-card pointer-events-auto flex items-start gap-3 p-4 rounded-xl shadow-2xl border border-slate-800/80 border-l-4 animate-toast-enter transition-all duration-300`;
    
    // Set color schemes & icons based on type
    let borderLeftColor = 'border-l-indigo-500';
    let iconColor = 'text-indigo-400';
    let iconSvg = `
        <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
    `;
    
    if (type === 'success') {
        borderLeftColor = 'border-l-emerald-500';
        iconColor = 'text-emerald-400';
        iconSvg = `
            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
        `;
    } else if (type === 'warning') {
        borderLeftColor = 'border-l-amber-500';
        iconColor = 'text-amber-400';
        iconSvg = `
            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
        `;
    } else if (type === 'error') {
        borderLeftColor = 'border-l-rose-500';
        iconColor = 'text-rose-400';
        iconSvg = `
            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
        `;
    }
    
    toast.classList.add(borderLeftColor);
    
    toast.innerHTML = `
        <div class="${iconColor} mt-0.5 flex-shrink-0">
            ${iconSvg}
        </div>
        <div class="flex-1 text-xs font-semibold text-slate-200 leading-normal">${message}</div>
        <button class="text-slate-400 hover:text-slate-200 focus:outline-none flex-shrink-0 transition ml-1 cursor-pointer" onclick="dismissToast(this)">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
        </button>
    `;
    
    container.appendChild(toast);
    
    // Auto remove after 4.5 seconds
    const timeoutId = setTimeout(() => {
        dismissElement(toast);
    }, 4500);
    
    toast.dataset.timeoutId = timeoutId;
}

function dismissToast(btn) {
    const toast = btn.closest('.glass-card');
    if (toast) {
        if (toast.dataset.timeoutId) {
            clearTimeout(parseInt(toast.dataset.timeoutId));
        }
        dismissElement(toast);
    }
}

function dismissElement(el) {
    if (el.parentNode) {
        el.classList.remove('animate-toast-enter');
        el.classList.add('animate-toast-exit');
        el.addEventListener('animationend', () => {
            el.remove();
        });
    }
}

// Helper to show filename upon selection
function updateFilename(inputId, labelId) {
    const input = document.getElementById(inputId);
    const label = document.getElementById(labelId);
    if (input.files && input.files.length > 0) {
        label.innerHTML = `<span class="font-semibold text-slate-200">Selected file:</span> ${input.files[0].name}`;
    } else {
        label.innerHTML = `<span class="font-semibold">Click to upload</span> or drag and drop`;
    }
}

// Fetch Metrics from Control Plane
async function fetchMetrics() {
    try {
        const res = await fetch(`${API_BASE}/metrics`);
        if (!res.ok) throw new Error("Failed to fetch metrics");
        const data = await res.json();
        
        document.getElementById('metric-resolved').textContent = data.resolved;
        document.getElementById('metric-investigating').textContent = data.investigating;
        document.getElementById('metric-escalated').textContent = data.escalated;
        document.getElementById('metric-runs').textContent = data.runs_without_incident;
    } catch (err) {
        console.error(err);
    }
}

// Fetch Incidents list
async function fetchIncidents() {
    try {
        const res = await fetch(`${API_BASE}/incidents`);
        if (!res.ok) throw new Error("Failed to fetch incidents");
        const incidents = await res.json();
        
        const tbody = document.getElementById('incidents-body');
        if (incidents.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="9" class="p-8 text-center text-slate-500">No incidents found in the platform control plane database.</td>
                </tr>
            `;
            return;
        }
        
        // Sorting incidents in reverse chronological order
        incidents.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        
        tbody.innerHTML = incidents.map(inc => {
            // Status badges
            let statusBadge = '';
            if (inc.status === 'RESOLVED') {
                statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">RESOLVED</span>';
            } else if (inc.status === 'INVESTIGATING' || inc.status === 'OPEN') {
                statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-sky-500/10 text-sky-400 border border-sky-500/20 animate-pulse">ACTIVE</span>';
            } else if (inc.status === 'ESCALATED') {
                statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/10 text-rose-400 border border-rose-500/20">ESCALATED</span>';
            } else {
                statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-400">${inc.status}</span>`;
            }
            
            // Severity badges
            let severityBadge = '';
            if (inc.severity === 'P0' || inc.severity === 'CRITICAL') {
                severityBadge = '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-500/20 text-rose-400">P0</span>';
            } else if (inc.severity === 'P1' || inc.severity === 'HIGH') {
                severityBadge = '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-orange-500/20 text-orange-400">P1</span>';
            } else if (inc.severity === 'P2' || inc.severity === 'MEDIUM') {
                severityBadge = '<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-amber-400">P2</span>';
            } else {
                severityBadge = `<span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-400">${inc.severity}</span>`;
            }

            const errorClassClean = inc.error_class || 'None';
            const errorMsgClean = inc.error_message ? (inc.error_message.length > 80 ? inc.error_message.substring(0, 80) + '...' : inc.error_message) : 'None';
            const rootCauseClean = inc.root_cause ? (inc.root_cause.length > 80 ? inc.root_cause.substring(0, 80) + '...' : inc.root_cause) : 'Under Investigation...';
            const actionClean = inc.recovery_action || 'Determining Action...';
            
            // Highlight selected row
            const isSelected = selectedIncidentId === inc.incident_id;
            const bgClass = isSelected ? 'bg-indigo-950/20 border-l-2 border-l-indigo-500' : 'hover:bg-slate-900/40';

            return `
                <tr class="transition duration-150 ${bgClass}">
                    <td class="p-4 font-mono font-bold text-indigo-300 select-all">${inc.incident_id}</td>
                    <td class="p-4 font-semibold">${inc.pipeline_id}</td>
                    <td class="p-4">${severityBadge}</td>
                    <td class="p-4 font-mono text-[10px] text-slate-400">${inc.category}</td>
                    <td class="p-4">${statusBadge}</td>
                    <td class="p-4 font-mono text-[10px] text-slate-400">${errorClassClean}</td>
                    <td class="p-4 text-slate-300 max-w-xs truncate" title="${inc.error_message || ''}">${errorMsgClean}</td>
                    <td class="p-4 text-slate-300 max-w-xs truncate" title="${inc.root_cause || ''}">${rootCauseClean}</td>
                    <td class="p-4 text-center">
                        <button onclick="selectIncident('${inc.incident_id}')" class="bg-indigo-600/10 hover:bg-indigo-600/30 text-indigo-400 border border-indigo-500/20 font-bold py-1 px-3 rounded text-[11px] transition cursor-pointer">
                            Audit
                        </button>
                    </td>
                </tr>
            `;
        }).join('');
        
        // Auto select first incident if none selected
        if (!selectedIncidentId && incidents.length > 0) {
            selectIncident(incidents[0].incident_id);
        }
    } catch (err) {
        console.error(err);
    }
}

// Select Incident to display its Audit Trail logs
async function selectIncident(incidentId) {
    selectedIncidentId = incidentId;
    document.getElementById('audit-incident-id').textContent = `Incident ID: ${incidentId}`;
    
    // Rerender table to update selection highlights
    fetchIncidents();
    fetchAuditLogs(incidentId);
}

// Fetch Audit Logs for incident
async function fetchAuditLogs(incidentId) {
    if (!incidentId) return;
    try {
        const res = await fetch(`${API_BASE}/incidents/${incidentId}/audit`);
        if (!res.ok) throw new Error("Failed to fetch audit trail");
        const trail = await res.json();
        
        const container = document.getElementById('audit-logs-container');
        if (trail.length === 0) {
            container.innerHTML = `<div class="text-center py-12 text-slate-500 text-xs">No audit logs recorded for incident ${incidentId}.</div>`;
            return;
        }
        
        // Color mapping matching Streamlit dashboard
        const colorMap = {
            "TELEMETRY_RECEIVER": "border-orange-500 text-orange-400",
            "MONITOR_AGENT": "border-sky-500 text-sky-400",
            "CLASSIFIER_AGENT": "border-purple-500 text-purple-400",
            "RCA_AGENT": "border-rose-500 text-rose-400",
            "RECOVERY_AGENT": "border-green-500 text-green-400",
            "ACTUATOR": "border-teal-500 text-teal-400",
            "CONTROL_PLANE": "border-slate-500 text-slate-400"
        };
        
        container.innerHTML = trail.map(step => {
            const comp = step.component || "UNKNOWN";
            const colorClass = colorMap[comp] || "border-slate-600 text-slate-300";
            
            // Format Timestamp
            let formattedTs = step.timestamp;
            try {
                const date = new Date(step.timestamp);
                formattedTs = date.toLocaleTimeString() + ' ' + date.toLocaleDateString();
            } catch(e){}

            return `
                <div class="p-4 border-l-4 rounded bg-slate-900/40 border border-slate-900 ${colorClass.split(' ')[0]} space-y-2">
                    <div class="flex justify-between items-center">
                        <span class="font-bold tracking-wide uppercase text-[11px] ${colorClass.split(' ')[1]}">${comp}</span>
                        <span class="text-[10px] text-slate-500">${formattedTs}</span>
                    </div>
                    <div class="font-mono text-slate-300 text-[11px] leading-relaxed whitespace-pre-wrap">${step.message}</div>
                </div>
            `;
        }).join('');
    } catch (err) {
        console.error(err);
    }
}

// Fetch Human Escalations
async function fetchEscalations() {
    try {
        const res = await fetch(`${API_BASE}/escalations`);
        if (!res.ok) throw new Error("Failed to fetch escalations");
        const list = await res.json();
        
        const container = document.getElementById('escalations-container');
        if (list.length === 0) {
            container.innerHTML = `<div class="text-center py-12 text-slate-500 text-xs">No manual escalations logged.</div>`;
            return;
        }
        
        container.innerHTML = list.map(item => {
            let formattedTs = item.timestamp;
            try {
                const date = new Date(item.timestamp);
                formattedTs = date.toLocaleTimeString() + ' ' + date.toLocaleDateString();
            } catch(e){}
            
            return `
                <div class="p-4 rounded-xl border border-rose-500/10 bg-rose-500/5 space-y-2">
                    <div class="flex justify-between items-center">
                        <span class="font-mono font-bold text-rose-400 select-all">${item.incident_id}</span>
                        <span class="text-[10px] text-slate-500">${formattedTs}</span>
                    </div>
                    <p class="text-slate-300 leading-normal text-[11px]">${item.reason}</p>
                </div>
            `;
        }).join('');
    } catch (err) {
        console.error(err);
    }
}

// Clear Database History
async function clearHistory() {
    if (!confirm("Are you sure you want to completely clear the control plane databases, incident history, and escalations list?")) return;
    try {
        const res = await fetch(`${API_BASE}/history/clear`, { method: 'POST' });
        if (!res.ok) throw new Error("Failed to clear history");
        const data = await res.json();
        
        showToast(data.message, "success");
        selectedIncidentId = null;
        document.getElementById('audit-logs-container').innerHTML = `<div class="text-center py-12 text-slate-500 text-xs">No incident selected. Click "Audit" on any incident above.</div>`;
        document.getElementById('audit-incident-id').textContent = `Select an incident to view audit`;
        
        refreshData();
    } catch (err) {
        showToast("Failed to reset history: " + err.message, "error");
    }
}

// Run Ingestion Pipeline A
async function runPipelineA() {
    const preset = document.getElementById('preset-pipeline-a').value;
    const fileInput = document.getElementById('upload-a');
    const resultBox = document.getElementById('result-a');
    const btn = document.getElementById('btn-pipeline-a');
    
    resultBox.className = "hidden text-xs rounded-lg p-3";
    resultBox.textContent = "";
    
    const formData = new FormData();
    if (fileInput.files.length > 0) {
        formData.append("file", fileInput.files[0]);
    } else if (preset) {
        formData.append("test_case", preset);
    } else {
        showToast("Warning: Please select a preloaded scenario or upload a custom JSON file first.", "warning");
        return;
    }
    
    // Disable button to prevent double-execution
    btn.disabled = true;
    btn.textContent = "Executing Ingestion Pipeline A... Please wait...";
    btn.classList.add('opacity-50', 'cursor-not-allowed');
    
    resultBox.classList.remove('hidden');
    resultBox.classList.add('bg-slate-900', 'text-slate-400');
    resultBox.textContent = "Executing Ingestion Pipeline A... Please wait...";
    
    try {
        const res = await fetch(`${API_BASE}/pipelines/a/execute`, {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        
        resultBox.className = "text-xs rounded-lg p-3 mt-2 ";
        if (res.ok && data.status === "success") {
            resultBox.classList.add('bg-emerald-500/10', 'text-emerald-400', 'border', 'border-emerald-500/20');
            resultBox.textContent = `Ingestion successful! Mapped and saved ${data.rows_ingested} records.`;
            showToast("Pipeline A execution completed successfully!", "success");
            
            // Clear inputs
            fileInput.value = "";
            document.getElementById('filename-a').innerHTML = `<span class="font-semibold">Click to upload</span> or drag and drop`;
        } else {
            resultBox.classList.add('bg-rose-500/10', 'text-rose-400', 'border', 'border-rose-500/20');
            resultBox.textContent = data.detail || data.message || "Pipeline execution failed. Telemetry reported an incident.";
            showToast("Pipeline A execution failed. Telemetry reported an incident.", "error");
        }
    } catch (err) {
        resultBox.className = "text-xs rounded-lg p-3 bg-rose-500/10 text-rose-400 border border-rose-500/20 mt-2";
        resultBox.textContent = "Error executing pipeline: " + err.message;
        showToast("Error executing Pipeline A: " + err.message, "error");
    } finally {
        // Re-enable button
        btn.disabled = false;
        btn.textContent = "Execute Ingestion Pipeline A";
        btn.classList.remove('opacity-50', 'cursor-not-allowed');
    }
    
    // Refresh lists immediately
    setTimeout(refreshData, 1000);
}

// Run Database Pipeline B
async function runPipelineB() {
    const preset = document.getElementById('preset-pipeline-b').value;
    const fileInput = document.getElementById('upload-b');
    const resultBox = document.getElementById('result-b');
    const btn = document.getElementById('btn-pipeline-b');
    
    resultBox.className = "hidden text-xs rounded-lg p-3";
    resultBox.textContent = "";
    
    const formData = new FormData();
    if (fileInput.files.length > 0) {
        formData.append("file", fileInput.files[0]);
    } else if (preset) {
        formData.append("test_case", preset);
    } else {
        showToast("Warning: Please select a preloaded scenario or upload a custom DB file first.", "warning");
        return;
    }
    
    // Disable button to prevent double-execution
    btn.disabled = true;
    btn.textContent = "Executing Ingestion Pipeline B... Please wait...";
    btn.classList.add('opacity-50', 'cursor-not-allowed');
    
    resultBox.classList.remove('hidden');
    resultBox.classList.add('bg-slate-900', 'text-slate-400');
    resultBox.textContent = "Executing Ingestion Pipeline B... Please wait...";
    
    try {
        const res = await fetch(`${API_BASE}/pipelines/b/execute`, {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        
        resultBox.className = "text-xs rounded-lg p-3 mt-2 ";
        if (res.ok && data.status === "success") {
            resultBox.classList.add('bg-emerald-500/10', 'text-emerald-400', 'border', 'border-emerald-500/20');
            resultBox.textContent = `Pipeline B ETL successful! Aggregated and stored ${data.rows_ingested} records.`;
            showToast("Pipeline B execution completed successfully!", "success");
            
            // Clear inputs
            fileInput.value = "";
            document.getElementById('filename-b').innerHTML = `<span class="font-semibold">Click to upload</span> or drag and drop custom .db file`;
        } else {
            resultBox.classList.add('bg-rose-500/10', 'text-rose-400', 'border', 'border-rose-500/20');
            resultBox.textContent = data.detail || data.message || "Pipeline B execution failed. Telemetry reported an incident.";
            showToast("Pipeline B execution failed. Telemetry reported an incident.", "error");
        }
    } catch (err) {
        resultBox.className = "text-xs rounded-lg p-3 bg-rose-500/10 text-rose-400 border border-rose-500/20 mt-2";
        resultBox.textContent = "Error executing pipeline: " + err.message;
        showToast("Error executing Pipeline B: " + err.message, "error");
    } finally {
        // Re-enable button
        btn.disabled = false;
        btn.textContent = "Execute Ingestion Pipeline B";
        btn.classList.remove('opacity-50', 'cursor-not-allowed');
    }
    
    // Refresh lists immediately
    setTimeout(refreshData, 1000);
}

// Master Refresh Function
function refreshData() {
    fetchMetrics();
    fetchIncidents();
    fetchEscalations();
    if (selectedIncidentId) {
        fetchAuditLogs(selectedIncidentId);
    }
}

// Run Initial loads
refreshData();

// Poll metrics and incident queues every 3 seconds
setInterval(refreshData, 3000);
