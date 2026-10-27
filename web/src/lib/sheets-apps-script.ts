/** The Apps Script an admin pastes into a Google Sheet so YMVA can add and look up rows
 * without a service-account key. YMVA posts JSON to it (services/tools.py _run_apps_script). */
export const SHEETS_APPS_SCRIPT = `// YMVA (Yellow Metal Voice Agent) — lets YMVA add and look up rows in this sheet.
// Deploy: Deploy > New deployment > Web app. Execute as: Me. Who has access: Anyone.
// Keep the web app URL private: anyone who has it can write to this sheet.

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(10000); // two calls at once must not write over each other
  try {
    return reply_(handle_(JSON.parse(e.postData.contents)));
  } catch (err) {
    return reply_({ ok: false, error: String((err && err.message) || err) });
  } finally {
    lock.releaseLock();
  }
}

function doGet() {
  return reply_({ ok: true, ymva: "ready" });
}

function handle_(req) {
  var book = req.spreadsheet_id
    ? SpreadsheetApp.openById(req.spreadsheet_id)
    : SpreadsheetApp.getActiveSpreadsheet();
  var tabs = book.getSheets();
  var sheet = null;
  if (req.sheet) {
    sheet = book.getSheetByName(req.sheet);
    if (!sheet) {
      var names = tabs.map(function (t) { return t.getName(); }).join(", ");
      return { ok: false, error: "The sheet has no tab named '" + req.sheet + "'. Its tabs: " + names };
    }
  } else if (req.gid !== null && req.gid !== undefined) {
    sheet = tabs.filter(function (t) { return t.getSheetId() === req.gid; })[0] || null;
  }
  sheet = sheet || tabs[0];

  var width = sheet.getLastColumn();
  var headers = width
    ? sheet.getRange(1, 1, 1, width).getDisplayValues()[0].map(function (h) { return String(h).trim(); })
    : [];

  if (req.action === "append_row") {
    var wanted = {};
    var missing = [];
    (req.columns || []).forEach(function (c) {
      wanted[c[0]] = c[1];
      if (headers.indexOf(c[0]) < 0 && missing.indexOf(c[0]) < 0) missing.push(c[0]);
    });
    if (missing.length) {
      // An empty sheet gets its title row; new titles are added after the last column.
      headers = headers.concat(missing);
      sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    }
    // A row YMVA added earlier in the same call: update it (blanks keep what's there).
    var row = Number(req.row);
    if (row >= 2 && row <= sheet.getLastRow()) {
      var cells = sheet.getRange(row, 1, 1, headers.length);
      var old = cells.getValues()[0];
      cells.setValues([headers.map(function (h, j) { return wanted[h] ? wanted[h] : old[j]; })]);
      return { ok: true, row: row };
    }
    sheet.appendRow(headers.map(function (h) { return h in wanted ? wanted[h] : ""; }));
    return { ok: true, row: sheet.getLastRow() };
  }

  if (req.action === "find_row") {
    var col = headers.indexOf(req.match_column);
    if (col < 0) return { ok: false, error: "The sheet has no column named " + req.match_column };
    var target = String(req.match_value || "").trim().toLowerCase();
    if (!target) return { ok: true, found: false };
    var rows = sheet.getDataRange().getDisplayValues();
    for (var i = 1; i < rows.length; i++) {
      if (String(rows[i][col]).trim().toLowerCase() === target) {
        var row = {};
        headers.forEach(function (h, j) { if (h) row[h] = rows[i][j]; });
        return { ok: true, found: true, row: row };
      }
    }
    return { ok: true, found: false };
  }

  return { ok: false, error: "Unknown action " + req.action };
}

function reply_(data) {
  return ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
}
`;
