/*
    Minimal manual mock for the browser WebSocket API, mirroring the style of
    mock.eventsource.js in this same directory: a hand-written class assigned onto
    global.WebSocket (jsdom itself implements no networking APIs), with helper
    methods the test drives directly instead of a real socket.

    terminalpage.js only ever assigns onopen/onmessage/onerror/onclose (not
    addEventListener), so that's all this needs to support.
*/

class MockWebSocket {
    constructor(url) {
        this.url = url;
        this.onopen = null;
        this.onmessage = null;
        this.onerror = null;
        this.onclose = null;
        this.readyState = MockWebSocket.CONNECTING;
        this.sent = [];
        MockWebSocket.instances.push(this);
    }

    send(data) {
        this.sent.push(data);
    }

    close() {
        this.readyState = MockWebSocket.CLOSED;
    }

    // Test-driven simulation helpers - a real WebSocket would call these itself.
    simulateOpen() {
        this.readyState = MockWebSocket.OPEN;
        if (typeof this.onopen === 'function') this.onopen({});
    }

    simulateMessage(dataObj) {
        if (typeof this.onmessage === 'function') this.onmessage({ data: JSON.stringify(dataObj) });
    }

    simulateClose(code = 1000, reason = '') {
        this.readyState = MockWebSocket.CLOSED;
        if (typeof this.onclose === 'function') this.onclose({ code, reason });
    }

    simulateError() {
        if (typeof this.onerror === 'function') this.onerror(new Error('socket error'));
    }
}

MockWebSocket.CONNECTING = 0;
MockWebSocket.OPEN = 1;
MockWebSocket.CLOSING = 2;
MockWebSocket.CLOSED = 3;

MockWebSocket.instances = [];
MockWebSocket.reset = () => { MockWebSocket.instances = []; };

module.exports = MockWebSocket;
