// Run from the repository root with: node tests/webserver/proxy_urls.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

for (const prefix of ['/', '/launch/fastdb-dev/']) {
    const requests = [];
    const context = vm.createContext({
        console,
        URL,
        document: {
            getElementById: () => ({appendChild() {}}),
            createElement: () => ({appendChild() {}}),
            createTextNode: () => ({}),
            querySelector: () => ({content: prefix}),
        },
        window: {location: {reload() {}}},
        XMLHttpRequest: class {
            open(method, url) { requests.push(url); }
            setRequestHeader() {}
            send() {}
        },
    });
    for (const file of [
        'src/webserver/static/fastdb_ns.js',
        'extern/rkwebutil/rkwebutil/static/rkwebutil.js',
        'extern/rkwebutil/rkwebutil/static/rkauth.js',
        'src/webserver/static/fastdb.js',
    ]) {
        // Exercise the actual classes without needing a DOM or module server.
        const source = fs.readFileSync(file, 'utf8')
            .replace(/^import .*$/gm, '').replace(/^export .*$/gm, '');
        vm.runInContext(source, context, {filename: file});
    }
    vm.runInContext(`
        const app = new fastdbap.Context();
        app.init();
        app.connector.sendHttpRequest('getprocvers', {}, () => {});
        app.auth.logout(() => {});
    `, context);
    assert.deepEqual(requests, [prefix + 'auth/isauth', prefix + 'getprocvers', prefix + 'auth/logout']);
    console.log(`API and authentication URL checks passed for ${prefix}`);
}
