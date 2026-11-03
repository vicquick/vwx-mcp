// VWX Bridge native pump — JS side.
// vwxBridge.pump() is a PromiseSync function: it executes on the Vectorworks
// main thread (job check ~µs; when jobs exist it runs vwx_pump.py). We poll
// it while idle; while a pump is in flight we simply wait for the promise —
// natural backpressure, no overlapping calls.

(function () {
  var POLL_MS = 250;
  var running = true;
  var done = 0;
  var elState = document.getElementById('state');
  var elQueue = document.getElementById('queue');
  var elLast  = document.getElementById('last');
  var elBtn   = document.getElementById('toggle');

  function setState(cls, text) { elState.className = cls; elState.textContent = text; }

  function tick() {
    if (!running) { setTimeout(tick, POLL_MS); return; }
    if (!window.vwxBridge || !window.vwxBridge.pump) {
      setState('err', 'bridge object missing');
      setTimeout(tick, 1000);
      return;
    }
    window.vwxBridge.pump()
      .then(function (res) {
        var q = (res && typeof res.jobs === 'number') ? res.jobs : 0;
        elQueue.textContent = q;
        if (q > 0) { elLast.textContent = new Date().toLocaleTimeString(); }
        if (res && res.paused) { setState('off', 'pausiert'); }
        else { setState('on', 'active'); }
        setTimeout(tick, POLL_MS);
      })
      .catch(function (err) {
        setState('err', 'error: ' + err);
        setTimeout(tick, 1500);
      });
  }

  elBtn.addEventListener('click', function () {
    running = !running;
    elBtn.textContent = running ? 'Pause' : 'Fortsetzen';
    window.vwxBridge.pump(!running).catch(function () {});
  });

  tick();
})();
