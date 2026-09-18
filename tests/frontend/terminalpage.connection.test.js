/**
 * Frontend unit tests for the ticket-based terminal connection flow in
 * templates/static/js/terminalpage.js (remotetunelling.md Phase 6/7).
 *
 * Covers: fetching a fresh single-use ticket from POST /terminal-session before every
 * (re)connect attempt, sending that ticket as the first WebSocket message rather than in the
 * URL, never sending ssh_host/ssh_port/ssh_username/ssh_password (the server resolves the
 * target from the ticket, not the client), and the explicit connection states
 * (device-offline / authorization-failed / session-expired / reconnecting).
 */

const path = require('path');
const MockWebSocket = require('./__mocks__/mock.websocket');

const {
    TerminalPageHandler,
} = require(path.join(__dirname, '..', '..', 'templates', 'static', 'js', 'terminalpage.js'));

function makeHandler(containerId = 'container-123') {
    const handler = new TerminalPageHandler();
    handler.terminalInfo = { id: containerId };
    handler.sshHash = 'fixed-test-hash';
    handler.term = { writeln: jest.fn() };
    handler.elements = {};
    return handler;
}

const SESSION = {
    websocket_url: 'wss://abcd.ngrok-free.app',
    ticket: 'a-real-ticket',
    expires_at: '2026-01-01T00:00:30+00:00',
};

describe('terminalpage connection logic', () => {
    beforeEach(() => {
        global.WebSocket = MockWebSocket;
        MockWebSocket.reset();
        global.fetch = jest.fn((url) => {
            if (url === '/terminal-session') {
                return Promise.resolve({ ok: true, json: async () => SESSION });
            }
            return Promise.resolve({ ok: true, json: async () => ({}) });
        });
    });

    afterEach(() => {
        delete global.WebSocket;
        delete global.fetch;
    });

    test('requests /terminal-session with the container id before ever opening a socket', async () => {
        const handler = makeHandler('container-123');

        await handler.connectToTerminal();

        expect(global.fetch).toHaveBeenCalledWith('/terminal-session', expect.objectContaining({
            method: 'POST',
        }));
        const [, opts] = global.fetch.mock.calls[0];
        expect(JSON.parse(opts.body)).toEqual({ container_id: 'container-123' });
        handler.stopActivityHeartbeat();
    });

    test('opens the WebSocket at the session URL and sends the ticket as the first message, never in the URL', async () => {
        const handler = makeHandler();

        await handler.connectToTerminal();
        const ws = MockWebSocket.instances[0];
        expect(ws.url).toBe(SESSION.websocket_url);
        expect(ws.url).not.toContain('ticket');
        expect(ws.url).not.toContain('token');

        ws.simulateOpen();

        expect(ws.sent).toHaveLength(1);
        expect(JSON.parse(ws.sent[0])).toEqual({ type: 'authenticate', data: { ticket: SESSION.ticket } });
        expect(handler.connectionState).toBe('authenticating');
        handler.stopActivityHeartbeat();
    });

    test('on "ready", sends sshConnect with only ssh_hash - never a client-chosen SSH target', async () => {
        const handler = makeHandler();
        await handler.connectToTerminal();
        const ws = MockWebSocket.instances[0];
        ws.simulateOpen();

        ws.simulateMessage({ type: 'ready', message: 'Server ready to accept commands' });

        expect(handler.connectionState).toBe('connected');
        const sshConnectMsg = JSON.parse(ws.sent[1]);
        expect(sshConnectMsg).toEqual({ type: 'sshConnect', data: { ssh_hash: 'fixed-test-hash' } });
        handler.stopActivityHeartbeat();
    });

    test('a 404 from /terminal-session sets authorization-failed and never opens a socket', async () => {
        global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({ error: 'not found' }) });
        const handler = makeHandler();

        await handler.connectToTerminal();

        expect(handler.connectionState).toBe('authorization-failed');
        expect(MockWebSocket.instances).toHaveLength(0);
    });

    test('a 409 from /terminal-session (device offline) sets device-offline and never opens a socket', async () => {
        global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ error: 'Device is offline' }) });
        const handler = makeHandler();

        await handler.connectToTerminal();

        expect(handler.connectionState).toBe('device-offline');
        expect(MockWebSocket.instances).toHaveLength(0);
    });

    test('a 4401 close (invalid/expired ticket) sets session-expired and fetches a brand new ticket', async () => {
        const handler = makeHandler();
        await handler.connectToTerminal();
        const firstWs = MockWebSocket.instances[0];
        firstWs.simulateOpen();

        const reconnectSpy = jest.spyOn(handler, 'connectToTerminal').mockImplementation(async () => {});
        firstWs.simulateClose(4401, 'Invalid or expired ticket');

        expect(handler.connectionState).toBe('session-expired');
        expect(reconnectSpy).toHaveBeenCalledTimes(1);
        handler.stopActivityHeartbeat();
    });

    test('an unexpected transport close after connecting schedules a reconnect, not an immediate retry', async () => {
        const handler = makeHandler();
        await handler.connectToTerminal();
        const firstWs = MockWebSocket.instances[0];
        firstWs.simulateOpen();

        const attemptReconnectSpy = jest.spyOn(handler, 'attemptReconnect').mockImplementation(() => {});
        firstWs.simulateClose(1006, 'abnormal closure');

        expect(attemptReconnectSpy).toHaveBeenCalledTimes(1);
        // Not the session-expired path - this is a transport failure, not a rejected ticket.
        expect(handler.connectionState).not.toBe('session-expired');
        handler.stopActivityHeartbeat();
    });

    test('attemptReconnect gives up after maxReconnectAttempts instead of retrying forever', () => {
        jest.useFakeTimers();
        const handler = makeHandler();
        handler.reconnectAttempts = handler.maxReconnectAttempts;
        const connectSpy = jest.spyOn(handler, 'connectToTerminal').mockImplementation(async () => {});

        handler.attemptReconnect();
        jest.runAllTimers();

        expect(handler.connectionState).toBe('error');
        expect(connectSpy).not.toHaveBeenCalled();
        jest.useRealTimers();
    });

    test('cleanup() suppresses auto-reconnect on the resulting close event', async () => {
        const handler = makeHandler();
        await handler.connectToTerminal();
        const ws = MockWebSocket.instances[0];
        ws.simulateOpen();
        ws.readyState = MockWebSocket.OPEN;

        const attemptReconnectSpy = jest.spyOn(handler, 'attemptReconnect').mockImplementation(() => {});
        handler.cleanup();
        ws.simulateClose(1000, 'client closed');

        expect(attemptReconnectSpy).not.toHaveBeenCalled();
        handler.stopActivityHeartbeat();
    });
});
