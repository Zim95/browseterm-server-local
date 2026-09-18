/**
 * TerminalsUtilities
 * Utility methods for terminals page functionality
 */
class TerminalsUtilities {
    /**
     * Get user info from window object (passed from backend)
     * @returns {Object} User info
     */
    static getUserInfo() {
        return window.userInfo || {};
    }

    static getOperatingSystems() {
        return window.images || [];
    }

    /**
     * Get this user's currently active device from window object (see
     * src/template_handlers.py:terminals) - resource controls are bounded by its remaining
     * quota now, not by a subscription plan.
     * @returns {Object|null} Device object, or null if none is active
     */
    static getActiveDevice() {
        return window.activeDevice || null;
    }

    /**
     * remotetunelling.md: "Play disabled when device/tunnel offline." Mirrors (approximately -
     * this is a UX hint only) Cloud's own real enforcement in
     * browseterm-server/src/cloud/terminal_handlers.py::_tunnel_is_online: the device must be
     * Active, its tunnel Online, and its last heartbeat recent. The exact staleness threshold
     * isn't exposed to the frontend, so this uses the same 90s default Cloud itself defaults to
     * (TUNNEL_OFFLINE_THRESHOLD_SECONDS) - a real Play attempt is always re-validated
     * server-side regardless of what this returns, so a mismatch here is only ever a stale-UI
     * annoyance, never a security gap.
     * @returns {boolean}
     */
    static isActiveDeviceTunnelOnline() {
        const device = TerminalsUtilities.getActiveDevice();
        if (!device || device.status !== 'Active' || device.tunnel_status !== 'Online') return false;
        if (!device.tunnel_last_heartbeat_at) return false;
        // The DB column is a naive UTC datetime, so Python's isoformat() on it (e.g.
        // "2026-09-18T17:17:43.389962") carries no timezone suffix at all. `new Date(...)` on a
        // date-time string with no offset parses it as the BROWSER's LOCAL time, not UTC (a
        // well-known JS Date gotcha) - in any timezone other than UTC this silently produces a
        // wildly wrong age and always reads as "offline". Force UTC interpretation, matching the
        // same "no tzinfo -> assume UTC" convention Cloud's own
        // terminal_handlers.py::_tunnel_is_online already uses server-side.
        const isoUtc = /[zZ]|[+-]\d{2}:?\d{2}$/.test(device.tunnel_last_heartbeat_at)
            ? device.tunnel_last_heartbeat_at
            : `${device.tunnel_last_heartbeat_at}Z`;
        const ageMs = Date.now() - new Date(isoUtc).getTime();
        return Number.isFinite(ageMs) && ageMs <= 90 * 1000;
    }

    /**
     * Normalize a display name into a safe username: lowercase, underscores, alphanumerics only
     * @param {string} name
     * @returns {string}
     */
    static normalizeName(name) {
        if (!name || typeof name !== 'string') return '';
        const lowered = name.trim().toLowerCase();
        const underscored = lowered.replace(/\s+/g, '_');
        return underscored.replace(/[^a-z0-9_]/g, '');
    }

    /**
     * Generate an alphanumeric password (no special chars)
     * @param {number} length
     * @returns {string}
     */
    static generatePassword(length = 8) {
        const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
        let pwd = '';
        for (let i = 0; i < length; i += 1) {
            const idx = Math.floor(Math.random() * chars.length);
            pwd += chars.charAt(idx);
        }
        return pwd;
    }

    /**
     * Find image record by name from window.images
     * @param {string} name
     * @returns {Object|null}
     */
    static findImageByName(name) {
        const images = TerminalsUtilities.getOperatingSystems();
        if (!Array.isArray(images)) return null;
        const target = (name || '').toString().trim();
        return images.find((img) => (img.name || '').toString().trim() === target) || null;
    }

    /**
     * Format status text (capitalize first letter)
     * @param {string} status - Terminal status
     * @returns {string} Formatted status
     */
    static formatStatus(status) {
        return status.charAt(0).toUpperCase() + status.slice(1);
    }

    /**
     * Adjust number input value, bounded by the input's OWN current min/max attributes (set
     * dynamically from the active device's remaining quota - see
     * TerminalsHandler.configureResourceControl) rather than a fixed default, so the +/- buttons
     * can never push a value past what the device actually has available. Dispatches a native
     * 'input' event afterward since setting .value programmatically doesn't fire one on its own -
     * this is what lets the Create button's own enabled/disabled state react immediately.
     * @param {HTMLInputElement} input - Input element
     * @param {number} change - Amount to change by
     */
    static adjustNumber(input, change) {
        const min = parseInt(input.min, 10) || 1;
        const max = parseInt(input.max, 10);
        const currentValue = parseInt(input.value, 10) || min;
        const newValue = currentValue + change;

        if (newValue >= min && (Number.isNaN(max) || newValue <= max)) {
            input.value = newValue;
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }

    /**
     * Show notification
     * @param {string} type - Notification type
     * @param {string} title - Notification title
     * @param {string} message - Notification message
     * @param {number} duration - Duration in milliseconds
     */
    static showNotification(type, title, message, duration) {
        if (typeof window.notifications === 'undefined' || window.notifications === null) {
            console.warn('Notification system not available');
            return;
        }
        if (typeof window.notifications[type] !== 'function') {
            console.warn('Notification type not available');
            return;
        }
        window.notifications[type](title, message, duration);
    }
}

/**
 * TerminalsHandler
 * Handles terminals page functionality
 */
class TerminalsHandler {
    /**
     * Initialize the terminals handler
     */
    constructor() {
        console.log('TerminalsHandler initialized');
        this.elements = {};
        this.terminals = [];
        this.operatingSystems = [];
        this.deviceQuota = null; // { availableCpu, availableMemoryGb, availableStorageGb } - see loadDeviceQuota()
        // Track containers pending "Running" status - auto-cleanup on failure
        // Map of containerId -> { kubernetesId, networkName, userId }
        this.pendingContainers = new Map();
        // Terminal IDs currently mid-hibernate (server-side save -> delete -> hibernate can take a
        // while) - shown with their own loading state regardless of the row's last-known status,
        // same idea as pendingContainers but for a manual action instead of creation.
        this.hibernatingIds = new Set();
    }

    /**
     * Initialize terminals page
     */
    async init() {
        console.log('Terminals page loaded successfully!');

        // Cache DOM elements
        this.cacheElements();

        // Load this device's remaining quota and bound CPU/Memory/Storage controls by it
        this.loadDeviceQuota();
        this.configureResourceControls();

        // Load terminals
        await this.loadTerminals();

        // Load operating systems for modal
        await this.loadOperatingSystems();

        // Setup event listeners
        this.setupEventListeners();

        // Setup SSE connection for real-time status updates
        this.setupStatusStream();
    }

    /**
     * Cache DOM elements
     */
    cacheElements() {
        this.elements = {
            terminalsList: document.getElementById('terminalsList'),
            newTerminalBtn: document.querySelector('.new-terminal-btn'),
            modalOverlay: document.getElementById('modalOverlay'),
            modalClose: document.getElementById('modalClose'),
            cancelBtn: document.getElementById('cancelBtn'),
            infoModalOverlay: document.getElementById('infoModalOverlay'),
            infoModalClose: document.getElementById('infoModalClose'),
            infoModalBody: document.getElementById('infoModalBody'),
            terminalForm: document.getElementById('terminalForm'),
            operatingSystemSelect: document.getElementById('operatingSystem'),
            cpuInput: document.getElementById('cpu'),
            cpuDecrease: document.getElementById('cpuDecrease'),
            cpuIncrease: document.getElementById('cpuIncrease'),
            memoryInput: document.getElementById('memory'),
            memoryDecrease: document.getElementById('memoryDecrease'),
            memoryIncrease: document.getElementById('memoryIncrease'),
            storageInput: document.getElementById('storage'),
            storageDecrease: document.getElementById('storageDecrease'),
            storageIncrease: document.getElementById('storageIncrease'),
            cpuQuota: document.getElementById('cpuQuota'),
            memoryQuota: document.getElementById('memoryQuota'),
            storageQuota: document.getElementById('storageQuota'),
            submitBtn: document.getElementById('submitBtn')
        };
    }

    /**
     * Load this device's remaining quota (available = allocated - used, see
     * browseterm-server/src/cloud/device_handlers.py's _serialize_device) from the backend.
     */
    loadDeviceQuota() {
        const device = TerminalsUtilities.getActiveDevice();
        if (!device) {
            this.deviceQuota = null;
            return;
        }
        const bytesToGb = (bytes) => Math.max(0, Math.floor(bytes / (1024 ** 3)));
        this.deviceQuota = {
            availableCpu: Math.max(0, device.available_cpu),
            availableMemoryGb: bytesToGb(device.available_memory_bytes),
            availableStorageGb: bytesToGb(device.available_storage_bytes),
        };
    }

    /**
     * Bound CPU/Memory/Storage controls by this device's remaining quota instead of subscription
     * plan - Cloud's own POST /containers is still the real enforcement (it validates + reserves
     * against the device's actual available capacity at creation time), this is only about
     * showing the user a realistic max up front.
     */
    configureResourceControls() {
        const quota = this.deviceQuota || { availableCpu: 0, availableMemoryGb: 0, availableStorageGb: 0 };
        this.configureResourceControl('cpu', quota.availableCpu);
        this.configureResourceControl('memory', quota.availableMemoryGb);
        this.configureResourceControl('storage', quota.availableStorageGb);
        this.updateSubmitButtonState();
    }

    /**
     * Configure a resource control (CPU, Memory, or Storage), bounding it by `available` (this
     * resource's remaining quota on the active device) and showing it as "/ <available> <unit>"
     * next to the +/- control.
     * @param {string} resource - 'cpu', 'memory', or 'storage'
     * @param {number} available - remaining quota for this resource
     */
    configureResourceControl(resource, available) {
        const decreaseBtn = this.elements[`${resource}Decrease`];
        const increaseBtn = this.elements[`${resource}Increase`];
        const input = this.elements[`${resource}Input`];
        const quotaDisplay = this.elements[`${resource}Quota`];
        const info = document.getElementById(`${resource}Info`);
        const min = parseInt(input.min, 10) || 1;
        // CPU is whole cores (Kubernetes cpu_limit convention); memory and storage are both
        // submitted with a "Gi" suffix (see handleFormSubmit) so GiB is the unit that actually
        // matches what gets requested, not an arbitrary display choice.
        const unit = resource === 'cpu' ? 'cores' : 'GiB';

        if (quotaDisplay) quotaDisplay.textContent = `/ ${available} ${unit}`;

        if (available >= min) {
            input.max = available;
            input.value = Math.min(parseInt(input.value, 10) || min, available);
            decreaseBtn.disabled = false;
            increaseBtn.disabled = false;
            input.disabled = false;
            if (info) info.style.display = 'none';
        } else {
            // Not enough remaining quota on this device for even the minimum size.
            decreaseBtn.disabled = true;
            increaseBtn.disabled = true;
            input.disabled = true;
            if (info) {
                info.textContent = 'ℹ️ Not enough device quota remaining for this resource';
                info.style.display = 'block';
            }
        }
    }

    /**
     * Load terminals from API
     */
    async loadTerminals() {
        try {
            const userInfo = TerminalsUtilities.getUserInfo();
            if (!userInfo.id) {
                console.log('No user ID available, cannot load terminals');
                this.terminals = [];
                this.renderTerminalsList();
                return;
            }

            const response = await fetch(`/list-user-containers?user_id=${userInfo.id}`);
            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Failed to load terminals');
            }

            // Transform API response to terminal format and sort by created_at descending
            this.terminals = (result.containers || [])
                .map(container => ({
                    id: container.id,
                    name: container.name,
                    ipAddress: container.ip_address || 'Pending...',
                    port: container.port_mappings?.[0]?.publish_port || '-',
                    status: container.status || 'Pending',
                    createdAt: container.created_at,
                    kubernetes_id: container.kubernetes_id || null
                }))
                .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));

            this.renderTerminalsList();
        } catch (error) {
            console.error('Error loading terminals:', error);
            this.showError('Error loading terminals. Please try again.');
            TerminalsUtilities.showNotification(
                'error',
                'Loading Error',
                'Failed to load terminals',
                5000
            );
        }
    }

    /**
     * Render terminals list
     */
    renderTerminalsList() {
        if (this.terminals.length === 0) {
            this.elements.terminalsList.innerHTML = 
                '<div class="loading-message">No terminals found.</div>';
            return;
        }

        const terminalsHTML = this.terminals
            .map(terminal => this.renderTerminalItem(terminal))
            .join('');

        this.elements.terminalsList.innerHTML = terminalsHTML;

        // Re-attach terminal control listeners
        this.attachTerminalControls();
    }

    /**
     * Get controls HTML based on terminal status
     * @param {string} terminalId - Terminal ID
     * @param {string} status - Terminal status (lowercase)
     * @param {string|null} kubernetesId - Kubernetes ID for K8s deletion
     * @returns {string} HTML string for controls
     */
    getControlsHTML(terminalId, status, kubernetesId = null) {
        // Hibernating (manual action, not a container `status` value of its own - see
        // hibernatingIds) takes priority over whatever the row's last-known status is.
        if (this.hibernatingIds.has(terminalId)) {
            return `
                <div class="terminal-loading">
                    <span class="loading-spinner"></span>
                    <span class="loading-text">Hibernating...</span>
                </div>`;
        }

        // Define controls configuration for each status
        const controlsConfig = {
            running: {
                showPlay: true,
                showInfo: true,
                showHibernate: true,
                showDelete: true,
                showLoading: false
            },
            failed: {
                showPlay: false,
                showInfo: true,
                showDelete: true,
                showLoading: false
            },
            pending: {
                showPlay: false,
                showDelete: false,
                showLoading: true
            },
            succeeded: {
                showPlay: false,
                showDelete: false,
                showLoading: true
            },
            unknown: {
                showPlay: false,
                showDelete: false,
                showLoading: true
            },
            hibernated: {
                showResume: true,
                showInfo: true,
                showDelete: true,
                showLoading: false
            },
            resuming: {
                showLoading: true
            }
        };

        const config = controlsConfig[status] || controlsConfig.pending;

        if (config.showLoading) {
            return `
                <div class="terminal-loading">
                    <span class="loading-spinner"></span>
                    <span class="loading-text">Creating...</span>
                </div>`;
        }

        let html = '';
        if (config.showPlay) {
            const deviceOnline = TerminalsUtilities.isActiveDeviceTunnelOnline();
            html += deviceOnline
                ? `
                <button class="control-btn play-btn" data-terminal-id="${terminalId}">
                    <i class="fas fa-play"></i>
                </button>`
                : `
                <button class="control-btn play-btn" data-terminal-id="${terminalId}" disabled
                        title="Your machine is offline - start BrowseTerm on it to open this terminal">
                    <i class="fas fa-play"></i>
                </button>`;
        }
        if (config.showInfo) {
            html += `
                <button class="control-btn info-btn" data-terminal-id="${terminalId}" title="Terminal info">
                    <i class="fas fa-circle-info"></i>
                </button>`;
        }
        if (config.showHibernate) {
            html += `
                <button class="control-btn hibernate-btn" data-terminal-id="${terminalId}" title="Hibernate">
                    <i class="fas fa-moon"></i>
                </button>`;
        }
        if (config.showResume) {
            html += `
                <button class="control-btn resume-btn" data-terminal-id="${terminalId}" title="Resume from snapshot">
                    <i class="fas fa-power-off"></i>
                </button>`;
        }
        if (config.showDelete) {
            html += `
                <button class="control-btn delete-btn" data-terminal-id="${terminalId}" data-kubernetes-id="${kubernetesId || ''}">
                    <i class="fas fa-trash"></i>
                </button>`;
        }
        return html;
    }

    /**
     * Render a single terminal item
     * @param {Object} terminal - Terminal data
     * @returns {string} HTML string for terminal item
     */
    renderTerminalItem(terminal) {
        // Status values: Pending, Running, Succeeded, Failed, Unknown, Hibernated, Resuming
        const statusLower = (terminal.status || 'Pending').toLowerCase();
        const statusText = terminal.status || 'Pending';
        const controlsHTML = this.getControlsHTML(terminal.id, statusLower, terminal.kubernetes_id);

        // Prefix a glyph on lifecycle states so they read at a glance:
        // moon = asleep (hibernated), spinning arrows = waking up (resuming).
        const statusIconMap = {
            hibernated: '<i class="fas fa-moon"></i> ',
            resuming: '<i class="fas fa-rotate spin-icon"></i> '
        };
        const statusIcon = statusIconMap[statusLower] || '';

        return `
            <div class="terminal-item" data-terminal-id="${terminal.id}">
                <div class="terminal-info">
                    <div class="terminal-name">${terminal.name}</div>
                    <div class="terminal-ip">
                        <div class="ip-address">${terminal.ipAddress || 'Pending...'}</div>
                        <div class="port">${terminal.port || '-'}</div>
                    </div>
                    <div class="terminal-status ${statusLower}">${statusIcon}${statusText}</div>
                </div>
                <div class="terminal-controls">
                    ${controlsHTML}
                </div>
            </div>
        `;
    }

    /**
     * Setup all event listeners
     */
    setupEventListeners() {
        // New terminal button
        if (this.elements.newTerminalBtn) {
            this.elements.newTerminalBtn.addEventListener('click', () => this.openModal());
        }

        // Modal close buttons
        if (this.elements.modalClose) {
            this.elements.modalClose.addEventListener('click', () => this.closeModal());
        }

        if (this.elements.cancelBtn) {
            this.elements.cancelBtn.addEventListener('click', () => this.closeModal());
        }

        if (this.elements.modalOverlay) {
            this.elements.modalOverlay.addEventListener('click', (e) => {
                if (e.target === this.elements.modalOverlay) {
                    this.closeModal();
                }
            });
        }

        // Info modal close buttons
        if (this.elements.infoModalClose) {
            this.elements.infoModalClose.addEventListener('click', () => this.closeInfoModal());
        }
        if (this.elements.infoModalOverlay) {
            this.elements.infoModalOverlay.addEventListener('click', (e) => {
                if (e.target === this.elements.infoModalOverlay) {
                    this.closeInfoModal();
                }
            });
        }

        // Form submission
        if (this.elements.terminalForm) {
            this.elements.terminalForm.addEventListener('submit', (e) => this.handleFormSubmit(e));
        }

        // CPU/Memory increment/decrement buttons
        this.setupNumberInputs();
    }

    /**
     * Attach event listeners to terminal control buttons
     */
    attachTerminalControls() {
        const playBtns = document.querySelectorAll('.play-btn');
        const infoBtns = document.querySelectorAll('.info-btn');
        const hibernateBtns = document.querySelectorAll('.hibernate-btn');
        const resumeBtns = document.querySelectorAll('.resume-btn');
        const deleteBtns = document.querySelectorAll('.delete-btn');

        playBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const terminalId = e.target.closest('button').getAttribute('data-terminal-id');
                this.handlePlay(terminalId);
            });
        });

        infoBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const terminalId = e.target.closest('button').getAttribute('data-terminal-id');
                this.handleInfo(terminalId);
            });
        });

        hibernateBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const terminalId = e.target.closest('button').getAttribute('data-terminal-id');
                this.handleHibernate(terminalId);
            });
        });

        resumeBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const terminalId = e.target.closest('button').getAttribute('data-terminal-id');
                this.handleResume(terminalId);
            });
        });

        deleteBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const button = e.target.closest('button');
                const terminalId = button.getAttribute('data-terminal-id');
                const kubernetesId = button.getAttribute('data-kubernetes-id');
                this.handleDelete(terminalId, kubernetesId);
            });
        });
    }

    /**
     * Setup number input controls (CPU/Memory/Storage). Listeners are always attached - whether
     * a control can actually be used is governed by its own `disabled` attribute (set in
     * configureResourceControl based on the active device's remaining quota), not by whether a
     * click handler exists at all. A disabled button never fires 'click', so this is sufficient.
     */
    setupNumberInputs() {
        if (this.elements.cpuDecrease && this.elements.cpuIncrease && this.elements.cpuInput) {
            this.elements.cpuDecrease.addEventListener('click', () => TerminalsUtilities.adjustNumber(this.elements.cpuInput, -1));
            this.elements.cpuIncrease.addEventListener('click', () => TerminalsUtilities.adjustNumber(this.elements.cpuInput, 1));
        }
        if (this.elements.memoryDecrease && this.elements.memoryIncrease && this.elements.memoryInput) {
            this.elements.memoryDecrease.addEventListener('click', () => TerminalsUtilities.adjustNumber(this.elements.memoryInput, -1));
            this.elements.memoryIncrease.addEventListener('click', () => TerminalsUtilities.adjustNumber(this.elements.memoryInput, 1));
        }
        if (this.elements.storageDecrease && this.elements.storageIncrease && this.elements.storageInput) {
            this.elements.storageDecrease.addEventListener('click', () => TerminalsUtilities.adjustNumber(this.elements.storageInput, -1));
            this.elements.storageIncrease.addEventListener('click', () => TerminalsUtilities.adjustNumber(this.elements.storageInput, 1));
        }

        // Re-validate the Create button every time any resource value changes - by the +/-
        // buttons (which dispatch a synthetic 'input' event, see adjustNumber) or by the user
        // typing directly into the (non-readonly-while-enabled) number input.
        [this.elements.cpuInput, this.elements.memoryInput, this.elements.storageInput].forEach((input) => {
            if (input) input.addEventListener('input', () => this.updateSubmitButtonState());
        });
    }

    /**
     * Create Terminal is only enabled when CPU, Memory, and Storage are all within the active
     * device's remaining quota (each input's own min/max, set from
     * available_cpu/available_memory_bytes/available_storage_bytes in configureResourceControl).
     * Cloud's POST /containers is still the real, authoritative check at creation time - this is
     * just keeping the button itself honest about what's likely to succeed.
     */
    updateSubmitButtonState() {
        if (!this.elements.submitBtn) return;
        const allWithinBounds = [this.elements.cpuInput, this.elements.memoryInput, this.elements.storageInput]
            .every((input) => this.isWithinDeviceQuota(input, parseInt(input && input.value, 10)));
        this.elements.submitBtn.disabled = !allWithinBounds;
    }

    /**
     * True if `value` is within `input`'s own current min/max (set from the active device's
     * remaining quota - see configureResourceControl). Shared by updateSubmitButtonState (live,
     * as the user types or clicks +/-) and handleFormSubmit's final check at Create time.
     * @param {HTMLInputElement} input
     * @param {number} value
     */
    isWithinDeviceQuota(input, value) {
        if (!input || Number.isNaN(value)) return false;
        const min = parseInt(input.min, 10) || 1;
        const max = parseInt(input.max, 10);
        return value >= min && (Number.isNaN(max) || value <= max);
    }

    /**
     * Setup SSE connection for real-time container status updates.
     *
     * P10 (see ~/browseterm/p.md's "P10" section): connects directly to Cloud's own
     * GET /events/stream, not to a Local-hosted relay - Local no longer polls or relays this at
     * all. Authenticated via a query-string sseToken (EventSource can't set custom headers);
     * there is no user_id in this URL at all - Cloud resolves the subscribing user from the
     * token's underlying session server-side, never from anything the client supplies.
     */
    setupStatusStream() {
        if (!window.sseToken || !window.cloudApiUrl) {
            console.log('No SSE token/Cloud URL available, skipping SSE setup');
            return;
        }

        let hasConnectedBefore = false;
        const eventSource = new EventSource(`${window.cloudApiUrl}/events/stream?token=${window.sseToken}`);

        eventSource.onopen = () => {
            console.log('SSE connection established for status updates');
            if (hasConnectedBefore) {
                // Reconnect after a drop - a status/save_status change could have happened while
                // disconnected. Per the plan's explicit P10 instruction: refetch full state to
                // repair missed events, don't try to replay them.
                this.loadTerminals();
            }
            hasConnectedBefore = true;
        };

        eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                console.log('SSE message received:', data);

                if (data.type === 'connected') {
                    console.log('SSE connected for user:', data.user_id);
                    return;
                }

                if (data.type === 'status_change') {
                    this.handleStatusChange(data);
                }
            } catch (error) {
                console.error('Error parsing SSE message:', error);
            }
        };

        eventSource.onerror = (error) => {
            console.error('SSE connection error:', error);
            // EventSource will auto-reconnect
        };

        // Store reference for cleanup if needed
        this.eventSource = eventSource;
    }

    /**
     * Handle container status change from SSE
     * @param {Object} data - Status change data
     */
    async handleStatusChange(data) {
        const { container_id, name, old_status, new_status } = data;
        console.log(`Container ${name} (${container_id}) status changed: ${old_status} -> ${new_status}`);

        // Find and update the terminal in our list
        const terminalIndex = this.terminals.findIndex(t => t.id === container_id);
        if (terminalIndex === -1) return;

        this.terminals[terminalIndex].status = new_status;
        this.renderTerminalsList();

        // Get pending info for this container
        const pendingInfo = this.pendingContainers.get(container_id);

        // Status handlers configuration
        const statusHandlers = {
            'Running': () => this.handleRunningStatus(container_id, name, pendingInfo),
            'Failed': () => this.handleFailedStatus(container_id, name, pendingInfo)
        };

        const handler = statusHandlers[new_status];
        if (handler) await handler();
    }

    /**
     * Handle container reaching Running status
     * @param {string} containerId - Container ID
     * @param {string} name - Container name
     * @param {Object|undefined} pendingInfo - Pending container info
     */
    handleRunningStatus(containerId, name, pendingInfo) {
        // Container is now running - remove from pending tracking
        if (pendingInfo) {
            this.pendingContainers.delete(containerId);
            console.log(`Container ${containerId} is now Running - removed from pending tracking`);
        }
        TerminalsUtilities.showNotification(
            'success',
            'Terminal Ready',
            `Terminal "${name}" is now running!`,
            4000
        );
    }

    /**
     * Handle container reaching Failed status
     * @param {string} containerId - Container ID
     * @param {string} name - Container name
     * @param {Object|undefined} pendingInfo - Pending container info
     */
    async handleFailedStatus(containerId, name, pendingInfo) {
        // Container failed - auto-cleanup only if it was pending
        if (!pendingInfo) {
            TerminalsUtilities.showNotification(
                'error',
                'Terminal Failed',
                `Terminal "${name}" failed.`,
                5000
            );
            return;
        }

        console.log(`Container ${containerId} failed before Running - auto-cleaning up...`);
        TerminalsUtilities.showNotification(
            'error',
            'Terminal Failed',
            `Terminal "${name}" failed to start. Cleaning up...`,
            5000
        );
        await this.cleanupFailedContainer(containerId, pendingInfo);
    }

    /**
     * Cleanup a failed container from K8s and DB
     * @param {string} containerId - Container DB ID
     * @param {Object} info - { kubernetesId, networkName, userId }
     */
    async cleanupFailedContainer(containerId, info) {
        try {
            console.log(`Cleaning up failed container ${containerId}...`);

            // Remove from pending tracking first
            this.pendingContainers.delete(containerId);

            // Step 1: Delete from K8s (if a pod was ever created). Passes the container's own DB
            // id (containerId), not info.kubernetesId - container-maker resolves the live pod by
            // its stable browseterm/container-id label (see delete_container_in_k8s's own
            // comment in src/api_handlers.py), not by a possibly-stale cached pod UID.
            if (info.kubernetesId) {
                await this.deleteContainerFromK8s(containerId, info.networkName);
            }

            // Step 2: Delete from DB
            await this.deleteContainerFromDB(containerId, info.userId);

            // Step 3: Refresh the list
            await this.loadTerminals();

            console.log(`Cleanup complete for container ${containerId}`);
        } catch (error) {
            console.error(`Error during cleanup of container ${containerId}:`, error);
            // Still try to refresh the list
            await this.loadTerminals();
        }
    }

    /**
     * Handle play button click
     * @param {string} terminalId - Terminal ID
     */
    handlePlay(terminalId) {
        console.log('Play button clicked for terminal:', terminalId);
        window.open(`/terminalpage?id=${terminalId}`, '_blank');
    }

    /**
     * Handle info button click — fetch this container's full row (src/api_handlers.py:
     * get_container_info) and show it in the info modal: resources, IP/port, status, timestamps.
     * @param {string} terminalId - Terminal DB ID
     */
    async handleInfo(terminalId) {
        try {
            const response = await fetch(`/get-container-info/${terminalId}`);
            const container = await response.json();
            if (!response.ok) {
                throw new Error(container.error || `HTTP ${response.status}`);
            }
            this.renderInfoModal(container);
            this.elements.infoModalOverlay.classList.add('active');
            document.body.style.overflow = 'hidden';
        } catch (error) {
            TerminalsUtilities.showNotification('error', 'Could Not Load Info', error.message, 5000);
        }
    }

    /**
     * Populate the info modal body from a container row.
     * @param {Object} container - Full container row from GET /get-container-info/{id}
     */
    renderInfoModal(container) {
        const portMappings = Array.isArray(container.port_mappings) ? container.port_mappings : [];
        const portsText = portMappings.length
            ? portMappings.map((p) => `${p.publish_port}→${p.target_port}/${p.protocol || 'TCP'}`).join(', ')
            : '-';
        const createdAt = container.created_at ? new Date(container.created_at).toLocaleString() : '-';
        const lastSavedAt = container.last_saved_at ? new Date(container.last_saved_at).toLocaleString() : 'Never';

        const rows = [
            ['Name', container.name || '-'],
            ['Status', TerminalsUtilities.formatStatus(container.status || 'Unknown')],
            ['CPU limit', container.cpu_limit ? `${container.cpu_limit} core(s)` : '-'],
            ['Memory limit', container.memory_limit || '-'],
            ['Storage limit', container.storage_limit || '-'],
            ['IP address', container.ip_address || 'Pending...'],
            ['Ports', portsText],
            ['Created', createdAt],
            ['Last saved', lastSavedAt],
        ];

        this.elements.infoModalBody.innerHTML = rows
            .map(([label, value]) => `
                <div class="info-row">
                    <span class="info-label">${label}</span>
                    <span class="info-value">${value}</span>
                </div>
            `)
            .join('');
    }

    /**
     * Close the info modal.
     */
    closeInfoModal() {
        this.elements.infoModalOverlay.classList.remove('active');
        document.body.style.overflow = '';
    }

    /**
     * Handle hibernate button click — a RUNNING container: save a fresh snapshot, tear down the
     * pod, and release this device's reserved capacity for it (src/api_handlers.py:
     * hibernate_container mirrors the reaper's own save-confirm-delete-hibernate ordering exactly,
     * just triggered manually instead of by idleness). Shows a per-row loading state for the
     * duration - this can take a while (a real snapshot build+push), same as Resume's own
     * synchronous wait.
     * @param {string} terminalId - Terminal DB ID
     */
    async handleHibernate(terminalId) {
        const terminal = this.terminals.find(t => t.id === terminalId);
        const terminalName = terminal?.name || 'this terminal';
        const confirmed = confirm(
            `Hibernate "${terminalName}"? This saves a snapshot, stops it, and frees up this device's resources. It can be resumed later.`
        );
        if (!confirmed) return;

        this.hibernatingIds.add(terminalId);
        this.renderTerminalsList();

        try {
            const resp = await fetch('/hibernate-container', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ container_id: terminalId })
            });
            const result = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                throw new Error(result.error || `HTTP ${resp.status}`);
            }
            TerminalsUtilities.showNotification('success', 'Hibernated', `Terminal "${terminalName}" is now hibernated.`, 4000);
            this.refreshDeviceQuota();
        } catch (e) {
            TerminalsUtilities.showNotification('error', 'Hibernate Failed', e.message, 6000);
        } finally {
            this.hibernatingIds.delete(terminalId);
            await this.loadTerminals();
        }
    }

    /**
     * Handle resume button click — a HIBERNATED container: recreate its pod from the saved
     * snapshot, then open the terminal.
     * @param {string} terminalId - Terminal DB ID
     */
    async handleResume(terminalId) {
        console.log('Resume button clicked for terminal:', terminalId);
        try {
            const resp = await fetch('/resume-container', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ container_id: terminalId })
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.error || `HTTP ${resp.status}`);
            }
            TerminalsUtilities.showNotification('success', 'Resuming', 'Restoring your workspace from its last snapshot…', 4000);
            await this.loadTerminals();
            window.open(`/terminalpage?id=${terminalId}`, '_blank');
        } catch (e) {
            TerminalsUtilities.showNotification('error', 'Resume failed', e.message, 6000);
        }
    }

    /**
     * Handle delete button click
     * @param {string} terminalId - Terminal DB ID
     * @param {string} kubernetesId - Kubernetes pod ID (from data attribute)
     */
    async handleDelete(terminalId, kubernetesId) {
        console.log('Delete button clicked for terminal:', terminalId, 'kubernetes_id:', kubernetesId);

        // Find the terminal to get its name for confirmation
        const terminal = this.terminals.find(t => t.id === terminalId);
        const terminalName = terminal?.name || 'Unknown';

        // Confirm deletion
        const confirmed = confirm(`Are you sure you want to delete terminal "${terminalName}"?`);
        if (!confirmed) {
            return;
        }

        const userInfo = TerminalsUtilities.getUserInfo();
        const networkName = `${userInfo.id}-namespace`;

        try {
            // Step 1: Delete from DB first
            await this.deleteContainerFromDB(terminalId, userInfo.id);

            // Step 2: Refresh the list immediately (so user sees it removed)
            await this.loadTerminals();

            TerminalsUtilities.showNotification(
                'info',
                'Terminal Deleted',
                `Terminal "${terminalName}" has been deleted.`,
                4000
            );

            // Step 3: Delete from K8s in the background (user doesn't need to wait). Passes
            // terminalId (the container's own DB id), not kubernetesId - container-maker
            // resolves the live pod by its stable browseterm/container-id label rather than
            // trusting this possibly-stale cached pod UID (see src/api_handlers.py's
            // delete_container_in_k8s comment). The DB row is already gone by this point (Step 1
            // above), which is fine: the label lives on the pod itself, not the DB row.
            if (kubernetesId) {
                this.deleteContainerFromK8s(terminalId, networkName).catch(err => {
                    console.error('Background K8s deletion failed:', err);
                });
            }

        } catch (error) {
            console.error('Error deleting terminal:', error);
            TerminalsUtilities.showNotification(
                'error',
                'Deletion Error',
                error.message,
                5000
            );
        }
    }

    /**
     * Open modal for creating new terminal. Refreshes device quota first (fire-and-forget is
     * fine here, but we await it so resetForm's clamping already has the latest numbers) - the
     * quota shown at page load can be stale if a terminal was created/hibernated/deleted earlier
     * in the same browser session.
     */
    async openModal() {
        this.elements.modalOverlay.classList.add('active');
        document.body.style.overflow = 'hidden';
        await this.refreshDeviceQuota();
        this.resetForm();
    }

    /**
     * Close modal
     */
    closeModal() {
        this.elements.modalOverlay.classList.remove('active');
        document.body.style.overflow = '';
    }

    /**
     * Re-fetches the active device's remaining quota from Local's own /device-quota (a thin
     * session-authenticated proxy to Cloud's internal active-device lookup - see
     * src/api_handlers.py:get_device_quota) and re-applies it to the resource controls/Create
     * button. Fails open (logs, leaves whatever quota was already loaded) rather than blocking
     * the modal on a transient network error.
     */
    async refreshDeviceQuota() {
        try {
            const response = await fetch('/device-quota');
            const data = await response.json();
            window.activeDevice = data.device;
        } catch (error) {
            console.error('Error refreshing device quota:', error);
        }
        this.loadDeviceQuota();
        this.configureResourceControls();
    }

    /**
     * Reset terminal creation form. Re-applies the current quota bounds afterward (not just the
     * static 1/1/2 defaults) so a device with less than that remaining still shows a valid,
     * in-bounds value and the Create button's state is correct immediately on open.
     */
    resetForm() {
        this.elements.terminalForm.reset();
        this.elements.cpuInput.value = 1;
        this.elements.memoryInput.value = 1;
        this.elements.storageInput.value = 2;
        this.configureResourceControls();
    }

    /**
     * Load operating systems for modal select
     */
    async loadOperatingSystems() {
        try {
            this.operatingSystems = TerminalsUtilities.getOperatingSystems();
            this.populateOperatingSystems();
        } catch (error) {
            console.error('Error loading operating systems:', error);
            this.elements.operatingSystemSelect.innerHTML = 
                '<option value="">Error loading OS</option>';
            TerminalsUtilities.showNotification(
                'error',
                'Loading Error',
                'Failed to load operating systems',
                5000
            );
        }
    }

    /**
     * Populate operating systems dropdown
     */
    populateOperatingSystems() {
        this.elements.operatingSystemSelect.innerHTML = '';

        if (this.operatingSystems.length === 0) {
            this.elements.operatingSystemSelect.innerHTML = 
                '<option value="">No operating systems available</option>';
            return;
        }

        this.operatingSystems.forEach(os => {
            const option = document.createElement('option');
            // Use the name as value so we can resolve by name later
            option.value = os.name;
            option.textContent = os.name;
            this.elements.operatingSystemSelect.appendChild(option);
        });
    }

    /**
     * Handle form submission
     * Two-step process:
     * 1. Create container in DB (fast) - closes modal and shows loading in terminal list
     * 2. Create container in K8s (slow) - happens in background, status updates via SSE
     * @param {Event} e - Form submit event
     */
    async handleFormSubmit(e) {
        e.preventDefault();

        // Get the submit button and show loading state
        const submitBtn = document.getElementById('submitBtn');
        const originalText = submitBtn.textContent;

        // Show loading spinner
        this.showLoadingState(submitBtn);

        try {
            // resolve user_id and generated username
            const userInfo = TerminalsUtilities.getUserInfo();
            const userName = userInfo.name || '';
            const generatedUsername = TerminalsUtilities.normalizeName(userName);

            // form data
            const formData = new FormData(e.target);

            // resolve image name from dropdown selection
            const selectedImageName = (formData.get('os') || '').toString();
            const imageRecord = TerminalsUtilities.findImageByName(selectedImageName);

            // Get resource values (use defaults for non-configurable)
            // Always read the actual form values now - no more subscription-tier gating on
            // whether the user's chosen CPU/Memory/Storage even gets used (see
            // configureResourceControl/updateSubmitButtonState for the real, device-quota-based
            // bound enforcement instead).
            const cpuValue = parseInt(formData.get('cpu'), 10) || 1;
            const memoryValue = parseInt(formData.get('memory'), 10) || 1;
            const storageValue = parseInt(formData.get('storage'), 10) || 2;

            // Final, authoritative-on-this-side check before ever calling the backend - the
            // Create button is already disabled whenever a value is out of bounds
            // (updateSubmitButtonState), but this guards the rare path where the button was
            // clicked in the instant before that state updated, or the form was submitted via
            // Enter. Cloud's own POST /containers still re-validates against the device's real
            // available capacity regardless (container_handlers.py's create_container) - this is
            // just a fast, friendly local check, not a replacement for that one.
            if (!this.isWithinDeviceQuota(this.elements.cpuInput, cpuValue)
                || !this.isWithinDeviceQuota(this.elements.memoryInput, memoryValue)
                || !this.isWithinDeviceQuota(this.elements.storageInput, storageValue)) {
                this.hideLoadingState(submitBtn, originalText);
                TerminalsUtilities.showNotification(
                    'error', 'Not Enough Quota',
                    'The requested CPU, Memory, or Storage exceeds this device\'s remaining quota.',
                    5000
                );
                return;
            }

            // Step 1: Create container in DB
            const dbData = {
                user_id: userInfo.id || '',
                image_id: imageRecord.id || '',
                name: (formData.get('name') || '').toString(),
                port_mappings: [{
                    publish_port: 2222,
                    target_port: 22,
                    protocol: 'TCP'
                }],
                environment_variables: {
                    SSH_USERNAME: generatedUsername,
                    SSH_PASSWORD: TerminalsUtilities.generatePassword(8)
                },
                cpu_limit: `${cpuValue}`,
                memory_limit: `${memoryValue}Gi`,
                storage_limit: `${storageValue}Gi`
            };

            const dbResponse = await fetch('/create-container-in-db', {
                method: 'POST',
                body: JSON.stringify(dbData),
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            const dbResult = await dbResponse.json();
            if (!dbResponse.ok) {
                throw new Error(dbResult.error);
            }
            console.log('Container created in DB:', dbResult);

            // Close modal immediately after DB success
            this.closeModal();

            // The DB-create step is also where Cloud reserved this terminal's cpu/memory/storage
            // against the active device's quota (see container_handlers.py's create_container) -
            // refresh so the NEXT terminal's form reflects what's actually left, not what was
            // available when this page first loaded.
            this.refreshDeviceQuota();

            // Add the new terminal to the list with loading state
            const newTerminal = {
                id: dbResult.id,
                name: dbResult.name,
                ipAddress: dbResult.ip_address || 'Pending...',
                port: dbResult.port_mappings?.[0]?.publish_port || '-',
                status: dbResult.status || 'Pending'
            };
            this.terminals.unshift(newTerminal);
            this.renderTerminalsList();

            // Show info notification
            TerminalsUtilities.showNotification(
                'info',
                'Terminal Queued',
                'Your terminal is being created. This may take a moment...',
                4000
            );

            // Step 2: Create container in K8s (fire and forget - status updates come via SSE)
            const k8sData = {
                container_id: dbResult.id,
                user_id: userInfo.id || '',
                image_id: imageRecord.id || '',
                container_name: (formData.get('name') || '').toString(),
                network_name: `${userInfo.id}-namespace`,
                exposure_level: 2,  // CLUSTER_LOCAL
                publish_information: [{
                    publish_port: 2222,
                    target_port: 22,
                    protocol: 'TCP'
                }],
                environment_variables: {
                    SSH_USERNAME: generatedUsername,
                    SSH_PASSWORD: dbData.environment_variables.SSH_PASSWORD,
                },
                resource_limits: {
                    cpu_limit: `${cpuValue}`,
                    memory_limit: `${memoryValue}Gi`,
                    storage_limit: `${storageValue}Gi`,
                    snapshot_size_limit: `${storageValue}Gi`
                }
            };

            // Step 2: Create container in K8s
            const k8sResponse = await fetch('/create-container-in-k8s', {
                method: 'POST',
                body: JSON.stringify(k8sData),
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            const k8sResult = await k8sResponse.json();

            if (k8sResult.error) {
                console.error('K8s creation error:', k8sResult.error);
                // K8s creation failed - delete from DB and refresh list
                await this.deleteContainerFromDB(dbResult.id, userInfo.id);
                await this.loadTerminals();
                TerminalsUtilities.showNotification(
                    'error',
                    'Creation Error',
                    k8sResult.error,
                    5000
                );
                return;
            }

            console.log('K8s creation successful:', k8sResult);

            // Step 3: Update container with K8s info
            const updateData = {
                filters: {
                    container_id: dbResult.id
                },
                data: {
                    kubernetes_id: k8sResult.container_id,
                    ip_address: k8sResult.container_ip,
                    associated_resources: k8sResult.associated_resources
                }
            };

            const updateResponse = await fetch('/update-container', {
                method: 'POST',
                body: JSON.stringify(updateData),
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            const updateResult = await updateResponse.json();

            if (updateResult.error) {
                console.error('Update container error:', updateResult.error);
                // Update failed - delete from K8s and DB, then refresh list. dbResult.id (the DB
                // id), not k8sResult.container_id (the pod's own UID) - container-maker resolves
                // the live pod by its stable browseterm/container-id label instead, which was
                // already stamped on this pod at creation time using this same DB id.
                await this.deleteContainerFromK8s(dbResult.id, `${userInfo.id}-namespace`);
                await this.deleteContainerFromDB(dbResult.id, userInfo.id);
                await this.loadTerminals();
                TerminalsUtilities.showNotification(
                    'error',
                    'Update Error',
                    updateResult.error,
                    5000
                );
                return;
            }

            console.log('Container updated with K8s info:', updateResult);

            // Update local terminal data with new IP and kubernetes_id
            const terminalIndex = this.terminals.findIndex(t => t.id === dbResult.id);
            if (terminalIndex !== -1) {
                this.terminals[terminalIndex].ipAddress = k8sResult.container_ip;
                this.terminals[terminalIndex].kubernetes_id = k8sResult.container_id;
                this.renderTerminalsList();
            }

            // Track this container as pending - will auto-cleanup if it fails before Running
            this.pendingContainers.set(dbResult.id, {
                kubernetesId: k8sResult.container_id,
                networkName: `${userInfo.id}-namespace`,
                userId: userInfo.id
            });
            console.log(`Container ${dbResult.id} added to pending tracking - will auto-cleanup on failure`);

        } catch (error) {
            console.error('Error submitting form:', error);
            TerminalsUtilities.showNotification(
                'error',
                'Submission Error',
                error.message,
                5000
            );
        } finally {
            // Hide loading state and restore button
            this.hideLoadingState(submitBtn, originalText);
        }
    }

    /**
     * Delete container from K8s
     * @param {string} containerId - Container ID (kubernetes_id)
     * @param {string} networkName - Network name
     */
    async deleteContainerFromK8s(containerId, networkName) {
        try {
            const response = await fetch('/delete-container-in-k8s', {
                method: 'POST',
                body: JSON.stringify({
                    container_id: containerId,
                    network_name: networkName
                }),
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            const result = await response.json();
            if (result.error) {
                console.error('K8s deletion error:', result.error);
            }
            return result;
        } catch (error) {
            console.error('Error deleting from K8s:', error);
        }
    }

    /**
     * Delete container from DB
     * @param {string} containerId - Container ID
     * @param {string} userId - User ID
     */
    async deleteContainerFromDB(containerId, userId) {
        try {
            const response = await fetch('/delete-container-in-db', {
                method: 'POST',
                body: JSON.stringify({
                    container_id: containerId,
                    user_id: userId
                }),
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            const result = await response.json();
            if (result.error) {
                console.error('DB deletion error:', result.error);
            }
            return result;
        } catch (error) {
            console.error('Error deleting from DB:', error);
        }
    }

    /**
     * Show loading state on button
     * @param {HTMLElement} button - Button element to show loading state
     */
    showLoadingState(button) {
        if (!button) return;
        button.disabled = true;
        button.innerHTML = `
            <span class="loading-spinner"></span>
            Creating Terminal...
        `;
        button.classList.add('loading');
    }

    /**
     * Hide loading state and restore button
     * @param {HTMLElement} button - Button element to restore
     * @param {string} originalText - Original button text
     */
    hideLoadingState(button, originalText) {
        if (!button) return;
        button.disabled = false;
        button.textContent = originalText;
        button.classList.remove('loading');
    }

    /**
     * Show error message in UI
     * @param {string} message - Error message
     */
    showError(message) {
        this.elements.terminalsList.innerHTML = 
            `<div class="loading-message error">${message}</div>`;
    }
}

// Export for unit tests (Node/Jest). No-op in the browser where `module` is undefined.
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { TerminalsUtilities, TerminalsHandler };
}

// Initialize terminals handler when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    console.log('Terminals page DOM is ready');
    const terminalsHandler = new TerminalsHandler();
    terminalsHandler.init();
});
