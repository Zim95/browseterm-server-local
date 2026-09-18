/**
 * remotetunelling.md required frontend test: "Play disabled when device/tunnel offline."
 *
 * TerminalsHandler.getControlsHTML() (templates/static/js/terminals.js) renders the per-row
 * control buttons for the containers list. For a running container it must render a disabled
 * play-btn when the active device's tunnel isn't online, since Cloud's own
 * create_terminal_session will reject the attempt anyway (browseterm-server's
 * _tunnel_is_online) - this is a UX nicety mirroring that server-side check, not a substitute
 * for it.
 */

const path = require('path');

const {
    TerminalsUtilities,
    TerminalsHandler,
} = require(path.join(__dirname, '..', '..', 'templates', 'static', 'js', 'terminals.js'));

function onlineDevice(overrides = {}) {
    return {
        status: 'Active',
        tunnel_status: 'Online',
        tunnel_last_heartbeat_at: new Date().toISOString(),
        ...overrides,
    };
}

describe('TerminalsUtilities.isActiveDeviceTunnelOnline()', () => {
    afterEach(() => {
        delete window.activeDevice;
    });

    test('true when the active device is Active with a fresh Online heartbeat', () => {
        window.activeDevice = onlineDevice();
        expect(TerminalsUtilities.isActiveDeviceTunnelOnline()).toBe(true);
    });

    test('false when there is no active device at all', () => {
        window.activeDevice = null;
        expect(TerminalsUtilities.isActiveDeviceTunnelOnline()).toBe(false);
    });

    test('false when tunnel_status is Offline', () => {
        window.activeDevice = onlineDevice({ tunnel_status: 'Offline' });
        expect(TerminalsUtilities.isActiveDeviceTunnelOnline()).toBe(false);
    });

    test('false when device status is not Active', () => {
        window.activeDevice = onlineDevice({ status: 'Inactive' });
        expect(TerminalsUtilities.isActiveDeviceTunnelOnline()).toBe(false);
    });

    test('false when the last heartbeat is stale (> 90s old)', () => {
        window.activeDevice = onlineDevice({
            tunnel_last_heartbeat_at: new Date(Date.now() - 120 * 1000).toISOString(),
        });
        expect(TerminalsUtilities.isActiveDeviceTunnelOnline()).toBe(false);
    });

    test('false when there is no heartbeat timestamp at all', () => {
        window.activeDevice = onlineDevice({ tunnel_last_heartbeat_at: null });
        expect(TerminalsUtilities.isActiveDeviceTunnelOnline()).toBe(false);
    });
});

describe('TerminalsHandler.getControlsHTML() play-button gating', () => {
    afterEach(() => {
        delete window.activeDevice;
    });

    test('a running container gets an enabled play-btn when the device is online', () => {
        window.activeDevice = onlineDevice();
        const handler = new TerminalsHandler();

        const html = handler.getControlsHTML('term-1', 'running');

        expect(html).toContain('play-btn');
        expect(html).not.toContain('disabled');
    });

    test('a running container gets a disabled play-btn when the device is offline', () => {
        window.activeDevice = onlineDevice({ tunnel_status: 'Offline' });
        const handler = new TerminalsHandler();

        const html = handler.getControlsHTML('term-1', 'running');

        expect(html).toContain('play-btn');
        expect(html).toContain('disabled');
    });

    test('a non-running container never gets a play-btn regardless of device status', () => {
        window.activeDevice = onlineDevice({ tunnel_status: 'Offline' });
        const handler = new TerminalsHandler();

        const html = handler.getControlsHTML('term-1', 'hibernated');

        expect(html).not.toContain('play-btn');
    });
});
