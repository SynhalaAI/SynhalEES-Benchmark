let recorderWindowId = null;

chrome.action.onClicked.addListener((clickedTab) => {
  // If a recorder window is already open, just focus it instead of opening another.
  if (recorderWindowId !== null) {
    chrome.windows.update(recorderWindowId, { focused: true }, () => {
      if (chrome.runtime.lastError) {
        recorderWindowId = null;
        openRecorderWindow(clickedTab);
      }
    });
    return;
  }

  openRecorderWindow(clickedTab);
});

function openRecorderWindow(clickedTab) {
  // Must call tabCapture synchronously within this click handler — the
  // user gesture (icon click) is what authorizes capturing this tab.
  // chrome:// / edge:// / the Web Store, etc. cannot be captured at all.
  const isCapturable = clickedTab && clickedTab.url &&
    /^(https?|ftp):\/\//i.test(clickedTab.url);

  if (!isCapturable) {
    chrome.windows.create({
      url: 'popup.html?tabError=' + encodeURIComponent('This page cannot be captured. Open a normal webpage tab first, then click the extension icon.'),
      type: 'popup',
      width: 400,
      height: 700
    }, (win) => { recorderWindowId = win.id; });
    return;
  }

  chrome.tabCapture.getMediaStreamId({ targetTabId: clickedTab.id }, (streamId) => {
    let url = 'popup.html';
    if (chrome.runtime.lastError || !streamId) {
      url += '?tabError=' + encodeURIComponent(chrome.runtime.lastError?.message || 'Could not capture this tab.');
    } else {
      url += '?streamId=' + encodeURIComponent(streamId) +
             '&tabTitle=' + encodeURIComponent(clickedTab.title || '');
    }

    chrome.windows.create({
      url,
      type: 'popup',
      width: 400,
      height: 700
    }, (win) => { recorderWindowId = win.id; });
  });
}

chrome.windows.onRemoved.addListener((closedId) => {
  if (closedId === recorderWindowId) {
    recorderWindowId = null;
  }
});
