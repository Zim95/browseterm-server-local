/**
 * Frontend unit tests for templates/static/js/login.js's desktop-login query-param threading
 * (threadDesktopParamsOntoProviderButtons) - the piece that lets a login page opened directly in
 * the system browser (browseterm-desktop's system-browser OAuth flow) carry target/desktop_port
 * through to the provider buttons, since a plain static <a href> can't do that on its own.
 *
 * Runs under Jest with the jsdom test environment.
 */

const path = require('path');

const { LoginHandler } = require(path.join(__dirname, '..', '..', 'templates', 'static', 'js', 'login.js'));

function setLocationSearch(search) {
    Object.defineProperty(window, 'location', {
        value: { origin: 'http://browseterm.local.com', search, href: `http://browseterm.local.com/login${search}` },
        writable: true,
    });
}

function renderProviderButtons() {
    document.body.innerHTML = `
        <a href="/auth/google" class="btn btn-google">Google</a>
        <a href="/auth/github" class="btn btn-github">GitHub</a>
    `;
}

describe('login.js desktop query-param threading', () => {
    beforeEach(() => {
        renderProviderButtons();
    });

    test('a plain (non-desktop) page load leaves the provider hrefs untouched', () => {
        setLocationSearch('');
        new LoginHandler().threadDesktopParamsOntoProviderButtons();

        expect(document.querySelector('.btn-google').getAttribute('href')).toBe('/auth/google');
        expect(document.querySelector('.btn-github').getAttribute('href')).toBe('/auth/github');
    });

    test('target=desktop with a port appends both params to every provider button', () => {
        setLocationSearch('?target=desktop&desktop_port=54321');
        new LoginHandler().threadDesktopParamsOntoProviderButtons();

        const googleHref = document.querySelector('.btn-google').getAttribute('href');
        const githubHref = document.querySelector('.btn-github').getAttribute('href');
        expect(googleHref).toBe('/auth/google?target=desktop&desktop_port=54321');
        expect(githubHref).toBe('/auth/github?target=desktop&desktop_port=54321');
    });

    test('target=desktop without a port leaves the hrefs untouched', () => {
        setLocationSearch('?target=desktop');
        new LoginHandler().threadDesktopParamsOntoProviderButtons();

        expect(document.querySelector('.btn-google').getAttribute('href')).toBe('/auth/google');
    });

    test('an unrelated target value leaves the hrefs untouched', () => {
        setLocationSearch('?target=local&desktop_port=54321');
        new LoginHandler().threadDesktopParamsOntoProviderButtons();

        expect(document.querySelector('.btn-google').getAttribute('href')).toBe('/auth/google');
    });
});
