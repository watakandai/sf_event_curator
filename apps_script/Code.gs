/**
 * Bay Area Adventure Club RSVPs, with a Google Sheet as the backend.
 *
 * The site (docs/index.html) reads counts from this web app and posts an
 * "I'm in" / "I'm out" to it. Every RSVP is a row in the sheet, and the
 * latest row per plan and name wins, so changing your mind is one more row
 * rather than an edit. Only counts go back to the site: the page is public,
 * so friends' names stay in the sheet, where only its owner can see them.
 *
 * When a plan reaches its minimum, the sheet's owner gets one email saying
 * to create the Partiful. The script runs as the owner, so that address is
 * never written down anywhere.
 *
 * Setup is in the README, under "Bay Area Adventure Club".
 */

// The published site. plans.json is read from here to check that a plan
// exists, what its minimum is and whether its RSVP deadline has passed.
const SITE = 'https://watakandai.github.io/sfevents/';
const SHEET_NAME = 'rsvps';
const TZ = 'America/Los_Angeles';
const DEFAULT_MIN = 3;

function doGet() {
  return json_({ counts: counts_() });
}

function doPost(e) {
  let body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return json_({ error: 'Bad request.' });
  }
  const planId = String(body.plan || '').slice(0, 200);
  const name = String(body.name || '').trim().replace(/\s+/g, ' ').slice(0, 40);
  const going = body.going !== false;
  if (!name) return json_({ error: 'Add your name first.' });

  const plan = plans_()[planId];
  if (!plan) return json_({ error: 'That plan is no longer listed.' });
  if (plan.deadline && today_() > plan.deadline) {
    return json_({ error: 'RSVPs for this one have closed.' });
  }

  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    sheet_().appendRow([new Date(), planId, name, going ? 'in' : 'out']);
    SpreadsheetApp.flush();
    const counts = counts_();
    if (going && (counts[planId] || 0) >= plan.min) notifyTipped_(planId, plan, counts[planId]);
    return json_({ counts: counts });
  } finally {
    lock.releaseLock();
  }
}

// Friends in per plan: the latest row for each plan and name decides.
function counts_() {
  const rows = sheet_().getDataRange().getValues().slice(1);
  const latest = {};
  for (const [, plan, name, status] of rows) {
    if (!plan || !name) continue;
    latest[plan + '\n' + String(name).trim().toLowerCase()] = status;
  }
  const counts = {};
  for (const [key, status] of Object.entries(latest)) {
    if (status !== 'in') continue;
    const plan = key.split('\n')[0];
    counts[plan] = (counts[plan] || 0) + 1;
  }
  return counts;
}

// The same id rule as planId() in docs/index.html - keep the two in step.
function planId_(p) {
  if (p.id) return String(p.id);
  const base = p.event || String(p.title || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  return p.date ? base + '@' + p.date : base;
}

function plans_() {
  const cache = CacheService.getScriptCache();
  const cached = cache.get('plans');
  if (cached) return JSON.parse(cached);
  const config = JSON.parse(UrlFetchApp.fetch(SITE + 'plans.json').getContentText());
  const plans = {};
  for (const p of config.plans || []) {
    plans[planId_(p)] = {
      title: p.title || p.event || '',
      min: p.min == null ? DEFAULT_MIN : Number(p.min),
      deadline: p.deadline || '',
    };
  }
  // Short, so a newly pushed plan takes RSVPs within a few minutes.
  cache.put('plans', JSON.stringify(plans), 300);
  return plans;
}

function notifyTipped_(planId, plan, count) {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('tipped:' + planId)) return;
  props.setProperty('tipped:' + planId, new Date().toISOString());
  MailApp.sendEmail(
    Session.getEffectiveUser().getEmail(),
    `It's on: ${plan.title} (${count} in)`,
    `${count} friends are in for ${plan.title}, and it needed ${plan.min}.\n\n`
      + `1. Open the site in host mode and press "Create Partiful": ${SITE}?host=1#club\n`
      + `2. Paste the Partiful link into this plan's "partiful" field in docs/plans.json and push.\n\n`
      + `Who's in is in the "${SHEET_NAME}" tab of the sheet.`
  );
}

function sheet_() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = book.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = book.insertSheet(SHEET_NAME);
    sheet.appendRow(['time', 'plan', 'name', 'status']);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function today_() {
  return Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd');
}

function json_(data) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}
