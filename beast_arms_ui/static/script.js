document.addEventListener('DOMContentLoaded', () => {
    // State
    let config = { objects: [], points: [] };
    let selectedObject = null;
    let selectedPoint = null;
    let isRecording = false;

    // Elements
    const objectsContainer = document.getElementById('objects-container');
    const pointsContainer = document.getElementById('points-container');
    const activeComboDisplay = document.getElementById('active-combo-display');
    const pickBtn = document.getElementById('pick-btn');
    const statusMsg = document.getElementById('status-message');
    
    const adminRecordObjSelect = document.getElementById('record-obj-select');
    const adminRecordPtSelect = document.getElementById('record-pt-select');
    const adminRecordBtn = document.getElementById('admin-record-btn');
    const adminRecordStatus = document.getElementById('admin-record-status');
    const adminCancelBtn = document.getElementById('admin-cancel-btn');
    const trajectoriesList = document.getElementById('trajectories-list');
    const teleopToggleBtn = document.getElementById('teleop-toggle-btn');
    let isTeleopOn = false;
    
    const adminModal = document.getElementById('admin-modal');
    const adminToggleBtn = document.getElementById('admin-toggle-btn');
    const closeModalBtn = document.getElementById('close-modal-btn');
    
    // Admin Inputs
    const newObjName = document.getElementById('new-obj-name');
    const newObjImage = document.getElementById('new-obj-image');
    const addObjBtn = document.getElementById('add-obj-btn');
    const newPtName = document.getElementById('new-pt-name');
    const addPtBtn = document.getElementById('add-pt-btn');

    // Fetch config
    async function loadConfig() {
        try {
            const res = await fetch('/api/config');
            config = await res.json();
            renderUI();
            populateAdminDropdowns();
            populateTrajectoriesList();
        } catch (err) {
            showStatus('Failed to load configuration.', 'error');
        }
    }

    function populateAdminDropdowns() {
        adminRecordObjSelect.innerHTML = '<option value="">-- Select Object --</option>' + 
            config.objects.map(o => `<option value="${o.id}">${o.name}</option>`).join('');
        adminRecordPtSelect.innerHTML = '<option value="">-- Select Point --</option>' + 
            config.points.map(p => `<option value="${p.id}">${p.name}</option>`).join('');
        updateAdminRecordState();
    }

    function renderUI() {
        // Render Objects
        objectsContainer.innerHTML = config.objects.map(obj => {
            const isRecorded = config.trajectories && config.trajectories.some(t => t.startsWith(obj.id + '_'));
            return `
            <div class="card obj-card ${selectedObject?.id === obj.id ? 'selected' : ''} ${!isRecorded ? 'disabled' : ''}" data-id="${obj.id}">
                <img src="/images/${obj.image}" alt="${obj.name}" class="obj-img" onerror="this.src='data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMDAiIGhlaWdodD0iMTAwIj48cmVjdCB3aWR0aD0iMTAwIiBoZWlnaHQ9IjEwMCIgZmlsbD0iIzMzMyIvPjwvc3ZnPg=='">
                <span class="card-title">${obj.name}</span>
                ${isRecorded ? '<span style="color:var(--success); font-size:0.8rem;">● Recorded</span>' : ''}
            </div>
        `}).join('');

        // Render Points
        pointsContainer.innerHTML = config.points.map(pt => {
            let isRecorded = false;
            if (selectedObject) {
                const comboId = `${selectedObject.id}_${pt.id}`;
                isRecorded = config.trajectories && config.trajectories.includes(comboId);
            }
            return `
            <div class="card pt-card ${selectedPoint?.id === pt.id ? 'selected' : ''} ${selectedObject && !isRecorded ? 'disabled' : ''}" data-id="${pt.id}">
                <span class="card-title">${pt.name}</span>
                ${selectedObject && isRecorded ? '<span style="color:var(--success); font-size:0.8rem;">● Recorded</span>' : ''}
            </div>
        `}).join('');

        attachCardListeners();
        updateSelectionState();
    }

    function attachCardListeners() {
        document.querySelectorAll('.obj-card').forEach(card => {
            card.addEventListener('click', () => {
                const id = card.dataset.id;
                selectedObject = config.objects.find(o => o.id === id);
                renderUI();
            });
        });

        document.querySelectorAll('.pt-card').forEach(card => {
            card.addEventListener('click', () => {
                const id = card.dataset.id;
                selectedPoint = config.points.find(p => p.id === id);
                renderUI();
            });
        });
    }

    function updateSelectionState() {
        if (selectedObject && selectedPoint) {
            const comboId = getComboId();
            const hasTraj = config.trajectories && config.trajectories.includes(comboId);
            activeComboDisplay.innerHTML = `<span class="combo-text">${selectedObject.name}</span> &nbsp;&rarr;&nbsp; <span class="combo-text">${selectedPoint.name}</span>`;
            
            if (hasTraj) {
                pickBtn.disabled = false;
            } else {
                pickBtn.disabled = true;
            }
        } else {
            activeComboDisplay.innerHTML = `<span class="placeholder">Select an object and point</span>`;
            pickBtn.disabled = true;
        }
    }

    function updateAdminRecordState() {
        const objId = adminRecordObjSelect.value;
        const ptId = adminRecordPtSelect.value;
        if (objId && ptId) {
            const comboId = `${objId}_${ptId}`;
            const hasTraj = config.trajectories && config.trajectories.includes(comboId);
            if (hasTraj) {
                adminRecordStatus.innerHTML = '<span style="color:var(--success)">(Recorded)</span>';
                if (!isRecording) adminRecordBtn.disabled = true;
            } else {
                adminRecordStatus.innerHTML = '<span style="color:var(--danger)">(Not Recorded)</span>';
                if (!isRecording) adminRecordBtn.disabled = false;
            }
        } else {
            adminRecordStatus.innerHTML = 'No selection';
            if (!isRecording) adminRecordBtn.disabled = true;
        }
    }

    adminRecordObjSelect.addEventListener('change', updateAdminRecordState);
    adminRecordPtSelect.addEventListener('change', updateAdminRecordState);

    function getComboId() {
        if (!selectedObject || !selectedPoint) return null;
        return `${selectedObject.id}_${selectedPoint.id}`;
    }

    function showStatus(msg, type='info') {
        statusMsg.textContent = msg;
        statusMsg.className = `status-message show status-${type}`;
        setTimeout(() => { statusMsg.classList.remove('show'); }, 3000);
    }

    // Teleop Toggle
    async function setTeleopState(state) {
        try {
            await fetch('/api/teleop/toggle', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({state: state ? 'on' : 'off'})
            });
            isTeleopOn = state;
            if (isTeleopOn) {
                teleopToggleBtn.textContent = 'Teleop: ON';
                teleopToggleBtn.style.borderColor = 'var(--success)';
                teleopToggleBtn.style.color = 'var(--success)';
            } else {
                teleopToggleBtn.textContent = 'Teleop: OFF';
                teleopToggleBtn.style.borderColor = 'var(--danger)';
                teleopToggleBtn.style.color = 'var(--danger)';
            }
        } catch (e) {
            showStatus('Failed to toggle teleop.', 'error');
        }
    }

    teleopToggleBtn.addEventListener('click', () => setTeleopState(!isTeleopOn));

    adminCancelBtn.addEventListener('click', async () => {
        try {
            await fetch('/api/record/cancel', { method: 'POST' });
            isRecording = false;
            adminRecordObjSelect.disabled = false;
            adminRecordPtSelect.disabled = false;
            adminRecordBtn.innerHTML = '<span class="icon">●</span> Start Recording';
            adminRecordBtn.classList.remove('recording');
            adminCancelBtn.style.display = 'none';
            adminRecordStatus.textContent = "Recording cancelled.";
            adminRecordStatus.style.color = "var(--danger)";
            setTeleopState(false);
        } catch (e) {
            showStatus('Failed to cancel recording.', 'error');
        }
    });

    async function deleteTrajectory(comboId) {
        if (!confirm('Are you sure you want to delete this trajectory?')) return;
        try {
            await fetch(`/api/trajectories/${comboId}`, { method: 'DELETE' });
            await loadConfig();
            adminRecordStatus.textContent = "Trajectory deleted.";
            adminRecordStatus.style.color = "var(--success)";
        } catch (err) {
            showStatus('Failed to delete trajectory', 'error');
        }
    }

    function populateTrajectoriesList() {
        if (!config.trajectories || config.trajectories.length === 0) {
            trajectoriesList.innerHTML = '<p style="color:var(--text-secondary);">No trajectories recorded.</p>';
            return;
        }
        trajectoriesList.innerHTML = config.trajectories.map(comboId => {
            const parts = comboId.split('_pt_');
            if (parts.length !== 2) return '';
            const objId = parts[0];
            const ptId = 'pt_' + parts[1];
            const obj = config.objects.find(o => o.id === objId);
            const pt = config.points.find(p => p.id === ptId);
            const name = (obj ? obj.name : objId) + ' &rarr; ' + (pt ? pt.name : ptId);
            
            return `
            <div style="display:flex; justify-content:space-between; align-items:center; padding:0.8rem; background:rgba(0,0,0,0.3); border:1px solid var(--border-color); border-radius:6px; margin-bottom:0.5rem;">
                <span style="font-weight:bold;">${name}</span>
                <button class="btn btn-outline btn-sm delete-traj-btn" data-id="${comboId}" style="border-color:var(--danger); color:var(--danger); padding:0.3rem 0.6rem;">Delete</button>
            </div>
            `;
        }).join('');
        
        document.querySelectorAll('.delete-traj-btn').forEach(btn => {
            btn.addEventListener('click', () => deleteTrajectory(btn.dataset.id));
        });
    }

    // Record / Pick Actions
    adminRecordBtn.addEventListener('click', async () => {
        const comboId = `${adminRecordObjSelect.value}_${adminRecordPtSelect.value}`;
        if (isRecording) {
            // Stop
            try {
                const res = await fetch('/api/record/stop', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({combo_id: comboId})
                });
                const data = await res.json();
                isRecording = false;
                adminRecordObjSelect.disabled = false;
                adminRecordPtSelect.disabled = false;
                adminRecordBtn.innerHTML = '<span class="icon">●</span> Start Recording';
                adminRecordBtn.classList.remove('recording');
                adminCancelBtn.style.display = 'none';
                showStatus(`Saved trajectory with ${data.points || 0} points!`, 'success');
                setTeleopState(false);
                loadConfig(); // Refresh config to get new trajectories
            } catch (e) {
                showStatus('Failed to stop recording.', 'error');
            }
        } else {
            // Start
            try {
                await fetch('/api/record/start', {method: 'POST'});
                isRecording = true;
                adminRecordObjSelect.disabled = true;
                adminRecordPtSelect.disabled = true;
                adminRecordBtn.disabled = false;
                adminRecordBtn.innerHTML = '<span class="icon">■</span> Stop Recording';
                adminRecordBtn.classList.add('recording');
                adminCancelBtn.style.display = 'block';
                setTeleopState(true);
                showStatus('Recording exoskeleton movements...', 'info');
            } catch (e) {
                showStatus('Failed to start recording.', 'error');
            }
        }
    });

    pickBtn.addEventListener('click', async () => {
        try {
            showStatus('Sending pick command...', 'info');
            pickBtn.disabled = true;
            setTeleopState(false);
            const res = await fetch('/api/replay', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({combo_id: getComboId()})
            });
            
            if (res.ok) {
                showStatus('Picking object...', 'success');
            } else {
                showStatus('Trajectory not found for this combo.', 'error');
            }
        } catch (e) {
            showStatus('Pick request failed.', 'error');
        } finally {
            setTimeout(() => { if (!isRecording && config.trajectories && config.trajectories.includes(getComboId())) pickBtn.disabled = false; }, 2000);
        }
    });

    // Admin Modal
    adminToggleBtn.addEventListener('click', () => adminModal.classList.add('active'));
    closeModalBtn.addEventListener('click', () => adminModal.classList.remove('active'));

    addObjBtn.addEventListener('click', async () => {
        const name = newObjName.value.trim();
        const image = newObjImage.value.trim();
        if (!name || !image) return showStatus('Fill all object fields', 'error');
        
        await fetch('/api/config/objects', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name, image})
        });
        newObjName.value = '';
        newObjImage.value = '';
        loadConfig();
        showStatus('Object added!', 'success');
    });

    addPtBtn.addEventListener('click', async () => {
        const name = newPtName.value.trim();
        if (!name) return showStatus('Fill point name', 'error');
        
        await fetch('/api/config/points', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name})
        });
        newPtName.value = '';
        loadConfig();
        showStatus('Point added!', 'success');
    });

    // Init
    loadConfig();
});
