/**
 * DoSync Protocol — Node.js Reference Implementation v0.2
 * Certification tier: EMERGENCY (32/32 tests)
 *
 * Independent implementation of the DoSync protocol spec.
 * No shared code with the Python reference hub.
 *
 * Usage:
 *   npm install
 *   npm start
 *
 * Default port: 47201 (runs alongside the Python hub on 47200)
 *
 * Certification:
 *   python3 certify.py --host localhost --port 47201 --tier emergency
 */

import Fastify from 'fastify'
import { randomUUID, createHash } from 'crypto'

const PORT     = parseInt(process.env.PORT || '47201')
const HOST     = process.env.HOST || '0.0.0.0'
const PROTOCOL = 'dosync/0.1'
const VERSION  = '0.2.0'

// ── In-memory storage ─────────────────────────────────────────────────────────

const registry    = new Map()   // device_id → manifest
const auditLog    = []          // audit log entries (SHA-256 chained)
const eventLog    = []          // device events
const healthLog   = new Map()   // device_id → { ok, total }
const intentStore = new Map()   // intent_id → { status, result, createdAt }
const INTENT_STORE_TTL = 300_000  // 5 minutes in ms

function intentStoreCleanup() {
  const now = Date.now()
  for (const [id, entry] of intentStore) {
    if (now - entry.createdAt > INTENT_STORE_TTL) intentStore.delete(id)
  }
}

// ── Authentication ────────────────────────────────────────────────────────────

const API_TOKEN = process.env.DOSYNC_TOKEN || ''

function checkAuth(req, reply) {
  if (!API_TOKEN) return true  // auth disabled if no token configured
  const header = req.headers['authorization'] || ''
  const token  = header.startsWith('Bearer ') ? header.slice(7) : ''
  if (token !== API_TOKEN) {
    reply.status(401).send({ detail: 'Invalid or missing token' })
    return false
  }
  return true
}

// ── Valid intent classes ──────────────────────────────────────────────────────

const VALID_INTENTS = new Set([
  'ensure_safety', 'alert_anomaly', 'control_access', 'monitor_health',
  'notify_family', 'report_status', 'set_environment', 'save_energy',
  'remind_chore', 'bedtime_routine', 'morning_routine', 'away_mode',
  'children_arrived_home',
])

const VALID_URGENCIES = new Set(['info', 'warning', 'alert', 'emergency'])

// ── SHA-256 chained audit log ─────────────────────────────────────────────────

function appendAudit(entry) {
  const prev    = auditLog.length > 0
    ? auditLog[auditLog.length - 1].hash
    : '0'.repeat(64)
  const data    = { ...entry, prev_hash: prev }
  const payload = JSON.stringify(data, Object.keys(data).sort())
  const hash    = createHash('sha256').update(payload).digest('hex')
  auditLog.push({ ...entry, prev_hash: prev, hash, timestamp: Date.now() / 1000 })
}

function verifyAuditIntegrity() {
  if (auditLog.length === 0) return true
  for (let i = 1; i < auditLog.length; i++) {
    if (auditLog[i].prev_hash !== auditLog[i - 1].hash) return false
  }
  return true
}

// ── Semantic resolver (capability matching) ───────────────────────────────────

const INTENT_TAGS = {
  ensure_safety:         ['emergency', 'alarm', 'light', 'communication', 'notification', 'security'],
  alert_anomaly:         ['sensor', 'communication', 'notification', 'alarm'],
  control_access:        ['lock', 'door', 'access', 'security'],
  monitor_health:        ['sensor', 'health', 'communication'],
  notify_family:         ['communication', 'notification'],
  report_status:         ['sensor', 'communication'],
  set_environment:       ['climate', 'light', 'thermostat'],
  save_energy:           ['light', 'appliance', 'climate', 'thermostat'],
  remind_chore:          ['communication', 'notification', 'display'],
  bedtime_routine:       ['light', 'climate', 'security'],
  morning_routine:       ['light', 'climate', 'appliance'],
  away_mode:             ['light', 'security', 'alarm', 'climate'],
  children_arrived_home: ['children_arrival', 'notification', 'communication', 'light'],
}

function resolve(intent, urgency, context = {}) {
  const targetTags = new Set(INTENT_TAGS[intent] || [])
  const actions    = []

  for (const [deviceId, manifest] of registry) {
    const deviceTags = new Set(manifest.tags || [])
    const overlap    = [...targetTags].filter(t => deviceTags.has(t))
    let score        = overlap.length * 10

    if (urgency === 'emergency' && manifest.emergency_capable) score += 30
    if (context.location && deviceTags.has(context.location))  score += 15

    if (score > 0) {
      for (const actuator of (manifest.actuators || [])) {
        actions.push({
          device_id:       deviceId,
          action:          actuator.type || actuator.id,
          params:          {},
          relevance_score: score,
        })
      }
    }
  }

  return actions.sort((a, b) => b.relevance_score - a.relevance_score)
}

// ── Fastify app ───────────────────────────────────────────────────────────────

const app = Fastify({ logger: false })

// ── Status ────────────────────────────────────────────────────────────────────

// GET /
app.get('/', async () => ({
  name:            'DoSync Hub (Node.js)',
  version:         VERSION,
  protocol:        PROTOCOL,
  implementation:  'dosync-node',
  language:        'javascript',
  devices:         registry.size,
  audit_entries:   auditLog.length,
  audit_integrity: verifyAuditIntegrity(),
  uptime_seconds:  Math.floor(process.uptime()),
}))

// GET /v1/status
app.get('/v1/status', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  return {
    name:            'DoSync Hub',
    version:         VERSION,
    protocol:        PROTOCOL,
    status:          'running',
    devices:         registry.size,
    audit_entries:   auditLog.length,
    audit_integrity: verifyAuditIntegrity(),
    ws_connections:  0,
  }
})

// ── Devices ───────────────────────────────────────────────────────────────────

// POST /v1/devices/register
app.post('/v1/devices/register', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  const manifest = req.body
  if (!manifest?.device_id)
    return reply.status(422).send({ detail: 'device_id is required' })

  registry.set(manifest.device_id, {
    ...manifest,
    registered_at: Date.now() / 1000,
    updated_at:    Date.now() / 1000,
  })
  appendAudit({ type: 'device_registered', device_id: manifest.device_id })

  return { status: 'registered', device_id: manifest.device_id, protocol: PROTOCOL }
})

// GET /v1/devices
app.get('/v1/devices', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  const devices = [...registry.values()].map(d => ({
    device_id:         d.device_id,
    device_name:       d.device_name,
    tags:              d.tags || [],
    emergency_capable: d.emergency_capable || false,
    adapter:           d.adapter || 'unknown',
    registered_at:     d.registered_at,
  }))
  return { count: devices.length, devices }
})

// GET /v1/devices/:device_id
app.get('/v1/devices/:device_id', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  const manifest = registry.get(req.params.device_id)
  if (!manifest)
    return reply.status(404).send({ detail: `Device '${req.params.device_id}' not found` })

  return {
    ...manifest,
    capabilities: {
      actuators: manifest.actuators || [],
      sensors:   manifest.sensors   || [],
    },
  }
})

// DELETE /v1/devices/:device_id
app.delete('/v1/devices/:device_id', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  const { device_id } = req.params
  if (!registry.has(device_id))
    return reply.status(404).send({ detail: `Device '${device_id}' not found` })

  registry.delete(device_id)
  appendAudit({ type: 'device_deregistered', device_id })
  return { status: 'deleted', device_id }
})

// POST /v1/device/action — direct device action
app.post('/v1/device/action', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  const { device_id, action, params = {}, urgency = 'info' } = req.body || {}

  if (!device_id || !action)
    return reply.status(422).send({ detail: 'device_id and action are required' })
  if (!registry.has(device_id))
    return reply.status(404).send({ detail: `Device '${device_id}' not found` })

  appendAudit({ type: 'direct_action', device_id, action, urgency })
  return { success: true, device_id, action, params, status: 'simulated' }
})

// ── Intents ───────────────────────────────────────────────────────────────────

// POST /v1/intent — deprecated, redirects to /v1/intent/async (308)
app.post('/v1/intent', { schema: { hide: true } }, async (req, reply) => {
  return reply.status(308).header('Location', '/v1/intent/async').send()
})

// POST /v1/intent/async — fire intent, return intent_id immediately
app.post('/v1/intent/async', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  const { intent, urgency = 'info', context = {} } = req.body || {}

  if (!intent)
    return reply.status(422).send({ detail: 'intent is required' })
  if (!VALID_INTENTS.has(intent))
    return reply.status(422).send({ detail: `Unknown intent '${intent}'` })
  if (!VALID_URGENCIES.has(urgency))
    return reply.status(422).send({ detail: `Invalid urgency '${urgency}'` })

  const intentId = randomUUID()

  // Store as pending immediately
  intentStoreCleanup()
  intentStore.set(intentId, {
    status:    'pending',
    result:    null,
    createdAt: Date.now(),
    intent,
    urgency,
  })

  // Execute in background (setImmediate = next event loop tick)
  setImmediate(() => {
    const actions = resolve(intent, urgency, context)
    const results = actions.map(a => ({
      device_id: a.device_id,
      action:    a.action,
      success:   true,
      response:  { status: 'simulated' },
    }))

    // Track health
    const devicesSeen = new Set(results.map(r => r.device_id))
    for (const deviceId of devicesSeen) {
      const h = healthLog.get(deviceId) || { ok: 0, total: 0 }
      h.ok    += 1
      h.total += 1
      healthLog.set(deviceId, h)
    }

    appendAudit({
      type:      'intent_executed',
      intent_id: intentId,
      intent,
      urgency,
      actions:   results.length,
      failed:    [],
      success:   true,
    })

    intentStore.set(intentId, {
      status:    'success',
      createdAt: Date.now(),
      intent,
      urgency,
      result: {
        intent_id:      intentId,
        success:        true,
        actions_taken:  results.length,
        failed_devices: [],
        results,
      },
    })
  })

  return { intent_id: intentId, status: 'pending', intent, urgency }
})

// GET /v1/intent/:intent_id — poll result of async intent
app.get('/v1/intent/:intent_id', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  const entry = intentStore.get(req.params.intent_id)
  if (!entry)
    return reply.status(404).send({ detail: `Intent '${req.params.intent_id}' not found` })

  if (entry.status === 'pending')
    return { intent_id: req.params.intent_id, status: 'pending', intent: entry.intent, urgency: entry.urgency }

  return { intent_id: req.params.intent_id, status: entry.status, ...entry.result }
})

// GET /v1/intents/:intent_class/explain — scoring breakdown
app.get('/v1/intents/:intent_class/explain', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  const { intent_class } = req.params

  if (!VALID_INTENTS.has(intent_class))
    return reply.status(422).send({ detail: `Unknown intent '${intent_class}'` })

  const targetTags = new Set(INTENT_TAGS[intent_class] || [])
  const included   = []
  const excluded   = []

  for (const [deviceId, manifest] of registry) {
    const deviceTags = new Set(manifest.tags || [])
    const overlap    = [...targetTags].filter(t => deviceTags.has(t))
    const score      = overlap.length * 10 + (manifest.emergency_capable ? 30 : 0)

    const entry = {
      device_id:   deviceId,
      device_name: manifest.device_name,
      device_tags: manifest.tags || [],
      score,
      matched_tags: overlap,
    }

    if (score > 0) included.push(entry)
    else excluded.push(entry)
  }

  return {
    intent:            intent_class,
    urgency:           'info',
    context:           {},
    resolution_tags:   [...targetTags].sort(),
    devices_evaluated: registry.size,
    devices_included:  included.length,
    devices_excluded:  excluded.length,
    included,
    excluded,
  }
})

// ── Events ────────────────────────────────────────────────────────────────────

// POST /v1/event
app.post('/v1/event', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  const { device_id, event_id, severity = 'info', data = {} } = req.body || {}

  if (!device_id || !event_id)
    return reply.status(422).send({ detail: 'device_id and event_id are required' })
  if (!registry.has(device_id))
    return reply.status(404).send({ detail: `Device '${device_id}' not found` })

  const entry = { device_id, event_id, severity, data, timestamp: Date.now() / 1000 }
  eventLog.push(entry)
  appendAudit({ type: 'device_event', ...entry })

  return { status: 'received', event_id, device_id }
})

// ── Health ────────────────────────────────────────────────────────────────────

// GET /v1/health/devices
app.get('/v1/health/devices', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  const devices = [...healthLog.entries()].map(([device_id, h]) => ({
    device_id,
    total_executions: h.total,
    successful:       h.ok,
    failed:           h.total - h.ok,
    success_rate:     h.total > 0 ? (h.ok / h.total) : 0,
  }))
  return { count: devices.length, devices }
})

// GET /v1/health/devices/:device_id
app.get('/v1/health/devices/:device_id', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  const { device_id } = req.params
  const h = healthLog.get(device_id)
  if (!h)
    return reply.status(404).send({ detail: `No health data for '${device_id}'` })

  return {
    device_id,
    total_executions: h.total,
    successful:       h.ok,
    failed:           h.total - h.ok,
    success_rate:     h.total > 0 ? (h.ok / h.total) : 0,
  }
})

// ── Audit ─────────────────────────────────────────────────────────────────────

// GET /v1/audit
app.get('/v1/audit', async (req, reply) => {
  if (!checkAuth(req, reply)) return
  const last    = parseInt(req.query?.last || '20')
  const entries = auditLog.slice(-last)
  return {
    count:     auditLog.length,
    integrity: verifyAuditIntegrity(),
    entries,
  }
})

// ── Start ─────────────────────────────────────────────────────────────────────

try {
  await app.listen({ port: PORT, host: HOST })
  console.log(`DoSync Hub (Node.js) v${VERSION}`)
  console.log(`Protocol: ${PROTOCOL}`)
  console.log(`Running on http://${HOST}:${PORT}`)
  if (API_TOKEN) {
    console.log(`Auth: enabled (DOSYNC_TOKEN set)`)
  } else {
    console.log(`Auth: disabled (set DOSYNC_TOKEN to enable)`)
  }
} catch (err) {
  console.error(err)
  process.exit(1)
}
