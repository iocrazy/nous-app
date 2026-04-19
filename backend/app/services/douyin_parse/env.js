// Node shim for douyin_bdms.js — compute a_bogus outside a browser.
// Usage:  node env.js "<full-douyin-url>"   → prints a_bogus to stdout

// -------- window + globals --------
globalThis.window = globalThis;

// -------- location / navigator / document / screen --------
window.location = {
    href: "https://www.douyin.com/",
    origin: "https://www.douyin.com",
    protocol: "https:",
    host: "www.douyin.com",
    hostname: "www.douyin.com",
    port: "",
    pathname: "/",
    search: "",
    hash: "",
};

// UA is supplied by the Python caller (abogus_parser._sign_with_node):
// either via argv[3] (passed to get_a_bogus) or via DOUYIN_UA env var.
// We do NOT keep a hardcoded default — a stale fallback here would
// silently desync from ua_pool.pick_ua() and break ABogus verify.
window.navigator = {
    userAgent: process.env.DOUYIN_UA || "",
    platform: "MacIntel",
    language: "en-US",
    languages: ["en-US", "en"],
    appName: "Netscape",
    appVersion: "5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    onLine: true,
    cookieEnabled: true,
    hardwareConcurrency: 12,
    deviceMemory: 32,
};

window.document = {
    all: undefined,
    documentElement: { style: {}, clientWidth: 2560, clientHeight: 1440 },
    body: { style: {} },
    cookie: "",
    domain: "www.douyin.com",
    referrer: "",
    createElement: function () {
        return { style: {}, setAttribute: function () {}, getElementsByTagName: function () { return []; } };
    },
    getElementById: function () { return null; },
    addEventListener: function () {},
    removeEventListener: function () {},
};

window.screen = {
    width: 2560, height: 1440,
    availWidth: 2560, availHeight: 1415,
    colorDepth: 24, pixelDepth: 24,
};

window.history = {
    length: 1,
    state: null,
    pushState: function () {},
    replaceState: function () {},
};

window.performance = globalThis.performance || {
    now: function () { return Date.now(); },
    timing: {},
    navigation: {},
};

window.__ac_referer = "";

// -------- XMLHttpRequest mock --------
// bdms will monkey-patch these methods after require(). The mock stores the
// URL on the instance so the sign path can read it.
function XMLHttpRequest() {
    this._method = null;
    this._url = null;
    this._async = true;
    this._headers = {};
    this._body = null;
    this.readyState = 0;
    this.status = 0;
    this.statusText = "";
    this.responseText = "";
    this.responseURL = "";
    this.withCredentials = false;
    this.onreadystatechange = null;
    this.onload = null;
    this.onerror = null;
    this.onprogress = null;
    this.onloadend = null;
    this.upload = { addEventListener: function () {} };
}
XMLHttpRequest.prototype.open = function (method, url, async) {
    this._method = method;
    this._url = url;
    this._async = async !== false;
    this.readyState = 1;
};
XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
    this._headers[name] = value;
};
XMLHttpRequest.prototype.send = function (body) {
    this._body = body;
};
XMLHttpRequest.prototype.abort = function () {};
XMLHttpRequest.prototype.getResponseHeader = function () { return null; };
XMLHttpRequest.prototype.getAllResponseHeaders = function () { return ""; };
XMLHttpRequest.prototype.addEventListener = function () {};
XMLHttpRequest.prototype.removeEventListener = function () {};

window.XMLHttpRequest = XMLHttpRequest;
globalThis.XMLHttpRequest = XMLHttpRequest;

// -------- Load bdms after the env is ready --------
require("./douyin_bdms");

// -------- Init bdms with Douyin web config --------
window.bdms.init({
    aid: 6383,
    pageId: 6241,
    paths: [
        "^/webcast/",
        "^/aweme/v1/",
        "^/aweme/v2/",
        "/douplus/",
        "/v1/message/send",
        "^/live/",
        "^/captcha/",
        "^/ecom/",
        "^/luna/pc",
    ],
    boe: false,
    ddrt: 8.5,
    ic: 8.5,
});

// -------- Sign function --------
function get_a_bogus(url, method, userAgent) {
    method = method || "GET";
    if (userAgent) window.navigator.userAgent = userAgent;

    window.a_bogus = undefined;

    var xhr = new XMLHttpRequest();
    xhr.open(method, url);
    xhr.setRequestHeader("User-Agent", window.navigator.userAgent);
    xhr.send(null);

    return window.a_bogus;
}

// -------- CLI entrypoint --------
if (require.main === module) {
    var DEMO_URL =
        "https://www.douyin.com/aweme/v1/web/aweme/detail/" +
        "?device_platform=webapp&aid=6383&aweme_id=7620732707982848933" +
        "&pc_client_type=1&version_code=190500&cookie_enabled=true&platform=PC";

    var url = process.argv[2] || DEMO_URL;
    var ua = process.argv[3] || process.env.DOUYIN_UA || "";
    if (!ua) {
        process.stderr.write(
            "error: DOUYIN_UA not provided — pass as argv[3] or DOUYIN_UA env var\n"
        );
        process.exit(3);
    }
    var bogus = get_a_bogus(url, "GET", ua);
    if (bogus === undefined) {
        process.stderr.write("a_bogus not computed — env shim incomplete\n");
        process.exit(2);
    }
    process.stdout.write(bogus + "\n");
    // bdms schedules background timers that keep the loop alive; force exit.
    process.exit(0);
}

module.exports = { get_a_bogus };