/**
 * DoSync Protocol — Node.js Reference Implementation
 * Tier: Basic (conectividad, registro de dispositivos, capability registry)
 *
 * Esta implementación es independiente del hub Python original.
 * Implementa la misma especificación desde cero en Node.js.
 *
 * Uso:
 *   npm install
 *   npm start
 *
 * Puerto por defecto: 47201 (para correr en paralelo con el hub Python en 47200)
 *
 * Certificación:
 *   python3 certify.py --host localhost --port 47201 --tier basic
 */

import Fastify from 'fastify'
import { randomUUID, createHash } from 'crypto'

const PORT     = parseInt(process.env.PORT || '47201')
const HOST     = process.env.HOST || '0.0.0.0'
const PROTOCOL = 'dosync/0.1'
const VERSION  = '0.1.0'

// ── Registry en memoria ───────────────────────────────────────────────────────

const registry = new Map()   // device_id → manifest
const auditLog = []          // entradas del audit log
const eventLog = []          // eventos recibidos de dispositivos

// ── Intent classes válidas ────────────────────────────────────────────────────

const VALID_INTENTS = new Set([
  'ensure_safety', 'alert_anomaly', 'control_access', 'monitor_health',
  'notify_family', 'report_status', 'set_environment', 'save_energy',
  'remind_chore', 'bedtime_routine', 'morning_routine', 'away_mode',
  'children_arrived_home',
])

const VALID_URGENCIES = new Set(['info', 'warning', 'alert', 'emergency'])

// ── Audit log con SHA-256 encadenado ─────────────────────────────────────────

function appendAudit(entry) {
  const prev    = auditLog.length > 0 ? auditLog[auditLog.length - 1].hash : '0'.repeat(64)
  const payload = JSON.stringify({ ...entry, prev_hash: prev }, Object.keys({ ...entry, prev_hash: prev }).sort())
  const hash    = createHash('sha256').update(payload).digest('hex')
  auditLog.push({ ...entry, prev_hash: prev, hash, timestamp: Date.now() / 1000 })
}

// ── Resolver básico (tag matching) ───────────────────────────────────────────

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

// GET / — Hub info
app.get('/', async () => ({
  name:          'DoSync Hub (Node.js)',
  version:       VERSION,
  protocol:      PROTOCOL,
  implementation:'dosync-node',
  language:      'javascript',
  devices:       registry.size,
  audit_entries: auditLog.length,
  uptime_seconds: Math.floor(process.uptime()),
}))

// POST /v1/devices/register
app.post('/v1/devices/register', async (req, reply) => {
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
app.get('/v1/devices', async () => {
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
  const manifest = registry.get(req.params.device_id)
  if (!manifest)
    return reply.status(404).send({ detail: `Device '${req.params.device_id}' not found` })

  return {
    ...manifest,
    capabilities: { actuators: manifest.actuators || [], sensors: manifest.sensors || [] },
  }
})

// POST /v1/intent
app.post('/v1/intent', async (req, reply) => {
  const { intent, urgency = 'info', context = {} } = req.body || {}

  if (!intent)
    return reply.status(422).send({ detail: 'intent is required' })
  if (!VALID_INTENTS.has(intent))
    return reply.status(422).send({ detail: `Unknown intent '${intent}'` })
  if (!VALID_URGENCIES.has(urgency))
    return reply.status(422).send({ detail: `Invalid urgency '${urgency}'` })

  const intentId = randomUUID()
  const actions  = resolve(intent, urgency, context)
  const results  = actions.map(a => ({
    device_id: a.device_id,
    action:    a.action,
    success:   true,
    response:  { status: 'simulated' },
  }))

  appendAudit({ type: 'intent_executed', intent_id: intentId, intent, urgency, actions_taken: results.length })

  return {
    success:        true,
    intent_id:      intentId,
    intent,
    urgency,
    actions_taken:  results.length,
    results,
    failed_devices: [],
  }
})

// POST /v1/event
app.post('/v1/event', async (req, reply) => {
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

// GET /v1/audit
app.get('/v1/audit', async (req) => {
  const last    = parseInt(req.query.last || '20')
  const entries = auditLog.slice(-last)
  let intact    = true
  for (let i = 1; i < auditLog.length; i++) {
    if (auditLog[i].prev_hash !== auditLog[i - 1].hash) { intact = false; break }
  }
  return { total: auditLog.length, intact, entries }
})

// ── Arrancar ──────────────────────────────────────────────────────────────────

try {
  await app.listen({ port: PORT, host: HOST })
  console.log(`DoSync Hub (Node.js) v${VERSION}`)
  console.log(`Protocol: ${PROTOCOL}`)
  console.log(`Running on http://${HOST}:${PORT}`)
} catch (err) {
  console.error(err)
  process.exit(1)
}
