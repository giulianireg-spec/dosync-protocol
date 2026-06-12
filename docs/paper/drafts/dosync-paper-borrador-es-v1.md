# DoSync: Una Capa Semántica para la Orquestación de Dispositivos IoT por Agentes de IA

**Rodrigo Giuliani**  
Investigador independiente  
Córdoba, Argentina  
rgiuliani@dosync.dev

---

## Resumen

Los protocolos IoT actuales fueron diseñados bajo el supuesto de que un humano decide qué hacer y un dispositivo ejecuta. Este modelo resulta inadecuado cuando se introducen agentes de IA, que expresan objetivos semánticos en lugar de comandos específicos. DoSync es un protocolo de comunicación abierto (Apache 2.0) que introduce una capa semántica entre agentes de IA y dispositivos físicos. En lugar de comandos, los agentes expresan intenciones (`ensure_safety`, `save_energy`, `children_arrived_home`) y cada dispositivo determina su rol automáticamente según sus capacidades declaradas en un Capability Manifest. El protocolo incluye un resolvedor semántico determinista, un motor de políticas configurable, y un log de auditoría resistente a manipulación mediante encadenamiento SHA-256. La implementación de referencia corre en una Raspberry Pi 5 con 38 dispositivos físicos reales, y el resolvedor opera en menos de 0.11ms p99 sobre el registry de producción. El protocolo escala hasta 5000 dispositivos dentro del límite de 500ms definido en la especificación. Se presentan dos implementaciones independientes — Python y Node.js — que demuestran que la especificación es suficientemente clara para ser implementada por terceros.

**Palabras clave:** IoT, agentes de IA, semántica, protocolo, orquestación, intención, MCP

---

## I. Introducción

La proliferación de dispositivos IoT y la emergencia de agentes de IA capaces han creado una brecha arquitectónica que los protocolos existentes no resuelven. Protocolos como Matter [1], Zigbee [2] y Z-Wave [3] fueron diseñados para el modelo humano-en-el-loop: un usuario decide qué hacer, una aplicación traduce esa decisión en un comando, y el dispositivo lo ejecuta. Este modelo funciona correctamente cuando el humano está presente en el proceso de decisión.

El problema emerge cuando se reemplaza al humano por un agente de IA. Un agente que detecta una caída mediante visión computacional no produce un comando `phone.call("911")` — produce una comprensión de la situación: "hay una emergencia". La traducción de esa comprensión a los comandos específicos de los dispositivos disponibles debe ser escrita por un desarrollador, manualmente, para cada escenario posible. Esta traducción se rompe cuando se agrega un nuevo dispositivo, no anticipa escenarios no previstos, y escala de manera insostenible con la complejidad del entorno.

Identificamos este problema como la *brecha de comandos*: la distancia arquitectónica entre el lenguaje de objetivos que producen los agentes de IA y el lenguaje de comandos que consumen los protocolos IoT existentes.

DoSync es un protocolo de comunicación abierto que cierra esta brecha introduciendo una capa semántica. Los agentes expresan intenciones estructuradas (`ensure_safety`, `save_energy`, `children_arrived_home`) en lugar de comandos. Cada dispositivo declara sus capacidades en un Capability Manifest al unirse a la red. Un resolvedor semántico determina qué dispositivos son relevantes para cada intención y construye un plan de acción en tiempo de ejecución, sin reglas pre-escritas.

Las contribuciones principales de este trabajo son:

1. La formalización del problema de la brecha de comandos en sistemas IoT con agentes de IA
2. El diseño e implementación de un protocolo de intención semántica con 13 clases de intención
3. Un resolvedor determinista basado en capacidades con resultados de benchmark sobre hardware de producción
4. Un motor de políticas configurable con 5 tipos de políticas para entornos críticos
5. Un log de auditoría resistente a manipulación mediante encadenamiento SHA-256
6. Dos implementaciones independientes (Python y Node.js) que validan la especificación

---

## II. Trabajo Relacionado

**Matter y protocolos de comandos.**  
Matter [1] es el estándar de interoperabilidad IoT más reciente, respaldado por Apple, Google, Amazon y Samsung. Define una API unificada de control de dispositivos basada en clusters y atributos. Su modelo es fundamentalmente orientado a comandos: un controlador envía `OnOff.On()` a un dispositivo específico. Matter no tiene concepto de intención semántica ni de resolución automática de dispositivos. La adición de soporte para agentes de IA requiere una capa de traducción externa.

**Home Assistant y automatizaciones.**  
Home Assistant [4] es la plataforma de automatización del hogar open source más adoptada. Su modelo de automatización se basa en reglas del tipo `if trigger then action`, que son esencialmente comandos diferidos. Si bien Home Assistant agregó soporte para MCP (Model Context Protocol) en 2025, el modelo subyacente sigue siendo orientado a comandos: el agente debe conocer los entity_ids específicos de los dispositivos. DoSync abstrae esta capa — de hecho, incluye un bridge nativo con Home Assistant que expone sus dispositivos como participantes del protocolo semántico.

**openHAB y binding de dispositivos.**  
openHAB [5] ofrece un modelo de abstracciones (Items, Things, Channels) que separa la lógica del control físico. Es más abstracto que Matter pero sigue requiriendo configuración manual de reglas. No existe un mecanismo de resolución automática basado en capacidades declaradas.

**Model Context Protocol (MCP).**  
MCP [6] es un protocolo abierto desarrollado por Anthropic que estandariza cómo los agentes de IA acceden a herramientas externas. DoSync implementa un servidor MCP nativo que expone el hub como conjunto de herramientas para cualquier agente compatible. La diferencia clave: MCP define el canal de comunicación entre agente y herramienta, mientras que DoSync define la semántica de las intenciones y la orquestación de dispositivos.

**Sistemas multi-agente y BDI.**  
La arquitectura BDI (Belief-Desire-Intention) [7] proporciona fundamentos teóricos para agentes que razonan sobre objetivos. DoSync puede interpretarse como una infraestructura de ejecución para la capa de intenciones en sistemas BDI aplicados a entornos físicos. Sin embargo, a diferencia de frameworks BDI completos, DoSync prioriza la determinismo, la auditabilidad y la seguridad sobre la capacidad de razonamiento compleja.

La diferencia fundamental con todos los sistemas anteriores: en DoSync, el conocimiento de *cómo responder* a una situación no vive en reglas centralizadas sino en los propios dispositivos, que declaran sus capacidades y contexto. El sistema ensambla la respuesta en tiempo de ejecución.

---

## III. Diseño del Sistema

### A. Arquitectura de 5 capas

DoSync organiza la comunicación en 5 capas con responsabilidades claramente separadas:

**Capa 5 — Intent:** el agente de IA expresa objetivos semánticos sin conocimiento de los dispositivos disponibles.

**Capa 4 — Semántica:** el resolvedor mapea la intención a un plan de acciones consultando el registry de capacidades.

**Capa 3 — Registry:** los dispositivos declaran sus capacidades al unirse a la red mediante un Capability Manifest.

**Capa 2 — Seguridad:** comunicación cifrada mediante TLS 1.3 con PKI local. No requiere internet.

**Capa 1 — Transporte (HAL):** abstracción sobre WiFi, BLE, Zigbee, Z-Wave y Thread.

### B. Capability Manifest

El Capability Manifest es la declaración formal de capacidades de un dispositivo:

```json
{
  "device_id": "lock-frontdoor-01",
  "tags": ["door-lock", "entrance", "emergency"],
  "actuators": [
    {"type": "unlock", "emergency_capable": true},
    {"type": "lock"}
  ],
  "sensors": [{"type": "contact", "id": "state"}],
  "emergency_capable": true,
  "adapter": "homeassistant"
}
```

Los `tags` son el mecanismo primario de resolución: permiten al resolvedor identificar qué dispositivos son relevantes para cada clase de intención sin configuración manual.

### C. Intent y clases de intención

Una intención semántica es una expresión estructurada de un objetivo:

```json
{
  "intent": "ensure_safety",
  "urgency": "emergency",
  "context": {"trigger": "fall_detected", "location": "bedroom"},
  "source": "vision_agent",
  "timestamp": 1748000000.0
}
```

El protocolo define 13 clases de intención organizadas por dominio:

| Clase | Dominio |
|---|---|
| `ensure_safety`, `alert_anomaly` | Seguridad (prioridad 1) |
| `control_access` | Acceso (prioridad 2) |
| `children_arrived_home`, `notify_family` | Presencia (prioridad 3) |
| `set_environment`, `morning_routine`, `bedtime_routine` | Confort (prioridad 4) |
| `save_energy`, `away_mode` | Eficiencia (prioridad 5) |
| `monitor_health`, `report_status`, `remind_chore` | Monitoreo/Recordatorio |

### D. Resolvedor Semántico

El resolvedor es el componente central de la Capa 4. Recibe una intención y devuelve un ActionPlan. La interfaz formal es:

```python
class BaseResolver:
    def resolve(self, intent: Intent) -> ActionPlan:
        raise NotImplementedError
```

La implementación de referencia (`CapabilityMatchingResolver`) calcula un score de relevancia para cada dispositivo registrado:

```
score = tag_overlap × 10 + location_match × 15 
      + emergency_bonus × 30 + actuator_match × 8
```

donde:
- `tag_overlap`: número de tags del dispositivo que intersectan con los tags de resolución de la intención
- `location_match`: 1 si el contexto incluye una ubicación que coincide con un tag del dispositivo
- `emergency_bonus`: 1 si la urgencia es `emergency` y el dispositivo es `emergency_capable`
- `actuator_match`: número de actuadores del dispositivo que coinciden con los requeridos por la intención

Los dispositivos con score > 0 se incluyen en el ActionPlan, ordenados por score descendente.

El `StateAwareResolver` extiende este comportamiento filtrando acciones redundantes: no enciende una luz que ya está al nivel de brillo solicitado, no desbloquea una puerta que ya está abierta. El estado se persiste en SQLite y sobrevive reinicios del hub.

El resolvedor es **determinista**: para la misma intención y el mismo registry, siempre produce el mismo resultado. Esta propiedad es esencial para entornos críticos donde el comportamiento impredecible es inaceptable.

### E. Motor de Políticas

El motor de políticas evalúa el ActionPlan antes de su ejecución. Soporta 5 tipos de política:

- **NeverAfterHoursPolicy:** bloquea actuadores fuera de horario configurado
- **RequireConfirmationPolicy:** requiere confirmación humana para acciones críticas
- **BlockIntentPolicy:** bloquea intenciones específicas por fuente
- **DeviceExclusionPolicy:** excluye dispositivos de ciertas intenciones
- **ConflictResolutionPolicy:** resuelve conflictos entre intenciones simultáneas por prioridad

Las intenciones de urgencia `emergency` bypasan todas las políticas excepto las de seguridad. Cada decisión del motor de políticas queda registrada en el log de auditoría.

### F. Log de Auditoría

Cada acción del hub genera una entrada en el log de auditoría. Las entradas están encadenadas mediante SHA-256:

```
hash_n = SHA256(entry_n || prev_hash_{n-1})
```

Esta cadena es resistente a manipulación: modificar cualquier entrada invalida todos los hashes subsecuentes. El hub verifica la integridad de la cadena en tiempo real. Este mecanismo es equivalente al usado en blockchain pero sin consenso distribuido — adecuado para auditoría local en entornos regulados.

---

## IV. Implementación

### A. Hub de referencia (Python)

El hub de referencia está implementado en Python con FastAPI. Expone 14 endpoints REST y un canal WebSocket para eventos en tiempo real. La persistencia usa SQLite con 6 tablas: `devices`, `audit_log`, `device_state`, `device_health`, `presence_signals`, y `api_keys`.

El hub corre en producción en una Raspberry Pi 5 como servicio systemd con TLS 1.3 habilitado mediante PKI local. La CA, los certificados del hub y los certificados de adapters externos son gestionados por el módulo `dosync.security`.

### B. Sistema de adapters

El hub nunca cambia para agregar soporte a un nuevo tipo de dispositivo. Cada fabricante implementa un adapter con un método:

```python
class DoSyncAdapter:
    async def execute(self, action: DeviceAction, 
                      urgency: Urgency) -> ActionResult:
        ...
```

Adapters disponibles: `WiZAdapter` (UDP local, Philips WiZ), `HABridge` (Home Assistant, 10 dominios), `NotificationAdapter` (SMS via Twilio), `GPIOAdapter` (Raspberry Pi, sensores PIR y DHT22).

### C. Servidor MCP nativo

DoSync incluye un servidor MCP nativo que expone el hub como conjunto de herramientas para cualquier agente de IA compatible. Las herramientas disponibles incluyen `dosync_fire_intent`, `dosync_get_status`, `dosync_list_devices`, `dosync_send_event`, y `dosync_get_audit_log`. Esto permite que agentes como Claude, ChatGPT o Hermes Agent controlen el hub directamente sin integración adicional.

### D. Segunda implementación — Node.js

Para validar que la especificación es suficientemente clara para ser implementada por terceros, se desarrolló una segunda implementación independiente en Node.js (sin código compartido con la implementación Python). Esta implementación pasa la suite de certificación tier Basic (6/6 tests), que incluye registro de dispositivos, resolución de intenciones y validación de manifests.

### E. Suite de certificación

El protocolo incluye una CLI de certificación con 16 tests organizados en 3 tiers:

- **Basic (6 tests):** conectividad, autenticación, registro de dispositivos
- **Standard (5 tests):** resolución de intenciones, eventos, validación de errores
- **Emergency (5 tests):** override de emergencia, integridad del log SHA-256

---

## V. Evaluación

### A. Setup experimental

Los experimentos se ejecutaron sobre el registry de producción: 38 dispositivos reales registrados en el hub (Raspberry Pi 5, Arm Cortex-A76, 8GB RAM). 500 iteraciones por resolvedor, semilla fija 42 para reproducibilidad. Las intenciones fueron muestreadas aleatoriamente sobre las 13 clases, 3 niveles de urgencia y 5 contextos de ubicación.

### B. Latencia del resolvedor

| Resolvedor | Media | Mediana | p95 | p99 |
|---|---|---|---|---|
| CapabilityMatchingResolver | 0.053ms | 0.047ms | 0.074ms | 0.107ms |
| StateAwareResolver | 0.053ms | 0.057ms | 0.084ms | 0.109ms |

Ambos resolvedores operan por debajo de 0.11ms p99 sobre el registry de producción. El `StateAwareResolver` elimina el 35% de acciones redundantes con latencia equivalente.

### C. Escalabilidad

| Dispositivos | Media | p95 | p99 | Dentro del límite (500ms) |
|---|---|---|---|---|
| 10 | 0.012ms | 0.017ms | 0.038ms | ✓ |
| 100 | 0.096ms | 0.141ms | 0.196ms | ✓ |
| 500 | 0.498ms | 0.737ms | 1.486ms | ✓ |
| 1000 | 1.013ms | 1.375ms | 3.044ms | ✓ |
| 5000 | 5.300ms | 9.129ms | 11.392ms | ✓ |

El resolvedor es O(n) sobre el registry. Todos los puntos de escala están dentro del límite de 500ms definido en la especificación. A 5000+ dispositivos, el p95 supera 9ms; una implementación con indexación por tag reduciría esto a O(1) — trabajo futuro planificado para v0.3.

### D. Overhead semántico

| Operación | Latencia media |
|---|---|
| Comando directo (dict lookup) | 0.0013ms |
| Resolución semántica (38 dispositivos) | 0.0529ms |
| **Overhead absoluto** | **0.051ms** |

En contexto de deployment real:

| Operación | Latencia típica |
|---|---|
| Resolución semántica | ~0.05ms |
| WiFi → WiZ (UDP) | 5–15ms |
| WiFi → Home Assistant (HTTP) | 20–80ms |
| **Capa semántica como % del total** | **< 1%** |

El overhead de la capa semántica es inferior al 1% del tiempo total de ejecución en cualquier deployment real.

### E. Log de auditoría

474 entradas acumuladas en el sistema de producción. Integridad verificada: la cadena SHA-256 está íntegra. El hub verifica la integridad en tiempo real en cada consulta al log.

---

## VI. Limitaciones y Trabajo Futuro

Identificamos las siguientes limitaciones del sistema actual:

**Scoring empírico.** Los pesos del algoritmo de scoring (10 puntos por tag, 30 por emergency bonus) fueron definidos empíricamente sobre los escenarios de producción. Una derivación formal o un proceso de optimización basado en datos históricos fortalecería la justificación del modelo.

**Estado no distribuido.** El `StateAwareResolver` persiste el estado del registry en SQLite local. En deployments con múltiples hubs, el estado no es consistente entre instancias. Una arquitectura de estado distribuido es necesaria para entornos industriales con múltiples zonas.

**Modelo de fallo parcial.** El protocolo no define el comportamiento cuando un adapter falla durante la ejecución de un ActionPlan parcialmente completado. El log de auditoría registra los fallos pero no existe un mecanismo de recuperación o compensación.

**Certificación por terceros.** La suite de certificación actual es auto-administrada. Un proceso de certificación formal por terceros es necesario para escenarios de seguridad crítica.

**Una sola implementación de producción.** La implementación Node.js pasa el tier Basic pero no Standard ni Emergency. Se requieren más implementaciones independientes para validar la especificación como estándar.

**Trabajo futuro planificado:**
- v0.3: indexación por tag (O(1) lookup), métricas de observabilidad
- v0.4: estado distribuido entre múltiples hubs, coordinación multi-agente
- v1.0: interfaz estable con semver, proceso de governance formal

---

## VII. Conclusión

DoSync demuestra que es posible introducir una capa semántica entre agentes de IA y dispositivos IoT sin sacrificar determinismo, auditabilidad ni seguridad. El protocolo resuelve la brecha de comandos: los agentes expresan objetivos, los dispositivos declaran capacidades, y el sistema ensambla la respuesta en tiempo de ejecución.

Los resultados empíricos muestran que la capa semántica agrega menos del 1% de overhead al tiempo total de ejecución en deployments reales, y escala hasta 5000 dispositivos dentro de los límites de la especificación.

La existencia de dos implementaciones independientes — en lenguajes diferentes, sin código compartido — valida que la especificación es suficientemente precisa para ser implementada por terceros. Este es el criterio mínimo para que un protocolo sea considerado un estándar abierto.

El código fuente, la especificación, y la suite de certificación están disponibles en: https://github.com/giulianireg-spec/dosync-protocol (Apache 2.0).

---

## Referencias

[1] Connectivity Standards Alliance, "Matter Specification v1.2," 2023.

[2] Zigbee Alliance, "Zigbee Specification," 2015.

[3] Z-Wave Alliance, "Z-Wave Specification," 2022.

[4] Home Assistant, "Home Assistant Architecture," https://www.home-assistant.io/docs/architecture/, 2024.

[5] openHAB Community, "openHAB Documentation," https://www.openhab.org/docs/, 2024.

[6] Anthropic, "Model Context Protocol Specification," https://modelcontextprotocol.io/, 2024.

[7] M. Wooldridge and N. R. Jennings, "Intelligent agents: Theory and practice," The Knowledge Engineering Review, vol. 10, no. 2, pp. 115–152, 1995.
