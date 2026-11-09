'use strict';
const tutorialEl = id => document.getElementById(id);
const tutorialLabels = ['Start small', 'Owner review', 'Design product', 'Check and publish', 'Use the queue', 'Wrong mappings'];
let tutorialToken = '';
let tutorialPath = 'tutorial';
let tutorialReady = false;
let tutorialStep = 0;
let tutorialWorking = false;

async function tutorialRequest(url, data) {
  const response = await fetch(url, data ? {method: 'POST', headers: {
    'Content-Type': 'application/json', 'X-Editor-Token': tutorialToken}, body: JSON.stringify(data)} : {});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}
function tutorialShowStep(index) {
  tutorialStep = index;
  document.querySelectorAll('[data-step]').forEach(node => node.classList.toggle('hidden', Number(node.dataset.step) !== index));
  [...tutorialEl('tutorialSteps').children].forEach((node, number) => {
    if (number === index) node.setAttribute('aria-current', 'step');
    else node.removeAttribute('aria-current');
  });
  tutorialEl('tutorialProgress').textContent = `Step ${index + 1} of 6`;
  tutorialEl('tutorialBack').disabled = index === 0;
  tutorialEl('tutorialNext').disabled = index === 5;
  sessionStorage.setItem('cadeTutorialStep', String(index));
}
function tutorialSync() {
  document.querySelectorAll('[data-spec]').forEach(node => {
    const path = [tutorialPath, node.dataset.spec, 'product.yaml'].filter(Boolean).join('/');
    node.href = `/?spec=${encodeURIComponent(path)}&stage=${node.dataset.stage}`;
    node.setAttribute('aria-disabled', String(!tutorialReady));
  });
  tutorialEl('tutorialInit').disabled = tutorialReady;
  document.querySelectorAll('[data-build], #tutorialPlan, #tutorialDiscover, #tutorialReport, #tutorialExercise, #ownerReview button').forEach(node => {node.disabled = !tutorialReady || tutorialWorking;});
}
async function tutorialRun(work) {
  if (tutorialWorking) return;
  tutorialWorking = true;
  tutorialSync();
  tutorialEl('tutorialInit').disabled = true;
  tutorialEl('tutorialStatus').textContent = 'Running the local action...';
  tutorialEl('tutorialResult').textContent = '';
  try { await work(); }
  catch (error) { tutorialEl('tutorialStatus').textContent = error.message; }
  finally { tutorialWorking = false; tutorialSync(); }
}
function tutorialOutput(message, result) {
  if (result.manifest) {
    const summary = {status: result.status, build_order: result.products,
      checks_passed: result.manifest.tests.filter(test => test.status === 'passed').length,
      checks_failed: result.manifest.tests.filter(test => test.status === 'failed').map(test => test.id),
      checks_not_run: result.manifest.tests.filter(test => test.status === 'not_run').length,
      manifest_path: result.manifest_path, acceptance_report_path: result.acceptance_report_path};
    if ('expected_failure' in result) Object.assign(summary, {expected_failure: result.expected_failure,
      publication_unchanged: result.publication_unchanged, support_queue: result.support_queue});
    result = summary;
  }
  tutorialEl('tutorialStatus').textContent = message;
  tutorialEl('tutorialResult').textContent = JSON.stringify(result, null, 2);
}
async function tutorialBuildAction(action, variant = 'product') {
  if (action === 'build' && !window.confirm('Build and publish into the local tutorial SQLite database?')) {
    tutorialEl('tutorialStatus').textContent = 'Build cancelled. No data changed.';
    return;
  }
  const path = [tutorialPath, variant, 'product.yaml'].filter(Boolean).join('/');
  const result = await tutorialRequest('/api/action', {action, path, profile: action === 'discover', confirm: action === 'build'});
  tutorialOutput(action === 'build' ? 'Build complete. Inspect the run status and acceptance evidence below.' : 'Check complete. Inspect the results below.', result);
}
tutorialLabels.forEach((label, index) => {
  const node = document.createElement('button');
  node.textContent = `${index + 1}. ${label}`;
  node.addEventListener('click', () => tutorialShowStep(index));
  tutorialEl('tutorialSteps').append(node);
});
document.querySelectorAll('[data-spec]').forEach(node => node.addEventListener('click', event => {
  if (!tutorialReady) {event.preventDefault(); tutorialEl('tutorialStatus').textContent = 'Create the local example in step 1 first.';}
}));
tutorialEl('tutorialBack').addEventListener('click', () => tutorialShowStep(Math.max(0, tutorialStep - 1)));
tutorialEl('tutorialNext').addEventListener('click', () => tutorialShowStep(Math.min(5, tutorialStep + 1)));
tutorialEl('tutorialInit').addEventListener('click', () => tutorialRun(async () => {
  const result = await tutorialRequest('/api/tutorial', {action: 'init'});
  tutorialPath = result.path === '.' ? '' : result.path;
  tutorialReady = true;
  tutorialOutput('Local data and contracts are ready. Build the starter next.', result);
}));
document.querySelectorAll('[data-build]').forEach(node => node.addEventListener('click', () => tutorialRun(() => tutorialBuildAction('build', node.dataset.build))));
tutorialEl('tutorialPlan').addEventListener('click', () => tutorialRun(() => tutorialBuildAction('plan')));
tutorialEl('tutorialDiscover').addEventListener('click', () => tutorialRun(() => tutorialBuildAction('discover')));
tutorialEl('tutorialReport').addEventListener('click', () => tutorialRun(async () => {
  tutorialOutput('Published support queue. Expect D3 at City Pharmacy, service S9.', await tutorialRequest('/api/tutorial', {action: 'report'}));
}));
tutorialEl('tutorialExercise').addEventListener('click', () => tutorialRun(async () => {
  if (!window.confirm('Build a wrong mapping in the local tutorial database and check that publication is blocked?')) {
    tutorialEl('tutorialStatus').textContent = 'Exercise cancelled. No data changed.';
    return;
  }
  const result = await tutorialRequest('/api/tutorial', {action: 'exercise', case: tutorialEl('tutorialCase').value, confirm: true});
  tutorialOutput(result.expected_failure && result.publication_unchanged ? 'Expected failure confirmed. All three published models are unchanged.' : 'Exercise did not meet its expected result. Inspect the evidence.', result);
}));
tutorialEl('ownerReview').addEventListener('submit', event => {
  event.preventDefault();
  tutorialRun(async () => {
    const result = await tutorialRequest('/api/tutorial', {action: 'review', reviewer: tutorialEl('reviewerName').value, decisions: tutorialEl('reviewDecisions').value});
    tutorialOutput('Owner discussion recorded. This note is not automatic approval.', result);
  });
});
tutorialShowStep(Math.max(0, Math.min(5, Number(sessionStorage.getItem('cadeTutorialStep')) || 0)));
tutorialSync();
tutorialRun(async () => {
  tutorialToken = (await tutorialRequest('/api/bootstrap')).token;
  const info = await tutorialRequest('/api/tutorial');
  tutorialReady = info.ready;
  tutorialPath = info.path === '.' ? '' : info.path;
  tutorialEl('tutorialStatus').textContent = tutorialReady ? 'Local example is ready. Your existing data and edits are kept.' : 'Start at step 1. Create the local example.';
});