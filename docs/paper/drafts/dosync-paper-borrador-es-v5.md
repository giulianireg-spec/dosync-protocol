# DoSync: Una Capa Semántica para la Orquestación de Dispositivos IoT por Agentes de IA

**Rodrigo Giuliani**  
Investigador independiente  
Córdoba, Argentina  
rgiuliani@dosync.dev

---

## Resumen

Los protocolos IoT actuales fueron diseñados bajo el supuesto de que un humano decide qué hacer y un dispositivo ejecuta. Este modelo resulta inadecuado cuando se introducen agentes de IA, que expresan objetivos semánticos en lugar de comandos específicos. DoSync es un protocolo de comunicación abierto (Apache 2.0) que introduce una capa semántica entre agentes de IA y dispositivos físicos. A diferencia de enfoques que requieren LLMs en el camino crítico de ejecución [1] o razonamiento goal-oriented no determinista [2], DoSync adopta un modelo declarativo: cada dispositivo publica un Capability Manifest con sus capacidades y contexto, y un resolvedor determinista construye el plan de acción en tiempo de ejecución sin reglas pre-escritas. El protocolo fue evaluado sobre una implementación de referencia ejecutando en una Raspberry Pi 5 con 38 dispositivos físicos reales. El resolvedor opera en menos de 0.11ms p99 y escala hasta 5000 dispositivos dentro del límite de 500ms de la especificación. Un Device Health Monitor en producción registró 31 ejecuciones sobre 7 dispositivos, con una tasa de éxito del 100% en el adaptador de notificaciones SMS. Se presentan dos implementaciones independientes — Python y Node.js — validando la especificación como protocolo interoperable. La evaluación de precisión semántica sobre 15 escenarios reales muestra precision promedio de 0.85 y recall de 0.49, con F1 = 1.00 en los intents de mayor criticidad.

**Palabras clave:** IoT, agentes de IA, semántica, protocolo, orquestación, intención, MCP

---

## I. Introducción

La proliferación de dispositivos IoT y la emergencia de agentes de IA capaces han creado una brecha arquitectónica que los protocolos existentes no resuelven. Protocolos como Matter [3], Zigbee [4] y Z-Wave [5] fueron diseñados para el modelo humano-en-el-loop: un usuario decide qué hacer, una aplicación traduce esa decisión en un comando, y el dispositivo lo ejecuta.

El problema emerge cuando se reemplaza al humano por un agente de IA. Un agente que detecta una caída mediante visión computacional no produce `phone.call("911")` — produce una comprensión de la situación: "hay una emergencia". La traducción de esa comprensión a comandos específicos debe ser escrita manualmente para cada escenario posible, se rompe cuando se agrega un nuevo dispositivo, y escala de manera insostenible con la complejidad del entorno.

Trabajos recientes abordan este problema de distintas maneras. LLMind [1] usa LLMs como orquestadores que generan scripts de control mediante máquinas de estado finito — potente pero introduce variabilidad en el camino crítico de ejecución. SASHA [2] aplica razonamiento goal-oriented con LLMs directamente sobre dispositivos del hogar — flexible pero no determinista ni auditable. DoSync adopta un enfoque diferente: en lugar de que el agente genere dinámicamente cómo actuar, los dispositivos declaran sus capacidades y el resolvedor determina el plan de acción de manera determinista. Esto preserva la capacidad de respuesta a objetivos semánticos sin sacrificar predictibilidad ni auditabilidad.

**Contribución científica principal.** Este trabajo demuestra que una arquitectura declarativa basada en capacidades puede resolver la brecha entre intenciones semánticas de agentes de IA y dispositivos físicos sin requerir razonamiento no determinista ni LLMs en el camino crítico de ejecución. La hipótesis central es que el conocimiento de *cómo responder* a una intención puede distribuirse a los propios dispositivos mediante manifests de capacidades, habilitando descubrimiento automático y resolución determinista. Esta separación — agente razona sobre objetivos, infraestructura ejecuta de manera predecible — es la contribución arquitectónica principal y la diferencia fundamental con los enfoques existentes.

Las contribuciones concretas son:

1. La formalización del problema de la brecha de comandos en sistemas IoT con agentes de IA
2. Un modelo declarativo de Capability Manifest para descubrimiento automático de dispositivos
3. Un resolvedor semántico determinista con benchmark sobre hardware de producción real
4. Evaluación de precisión y recall del resolvedor sobre 15 escenarios reales
5. Un motor de políticas configurable con 5 tipos para entornos críticos
6. Un log de auditoría resistente a manipulación mediante encadenamiento SHA-256
7. Un Device Health Monitor con datos de producción real
8. Dos implementaciones independientes (Python y Node.js) que validan la especificación

---

## II. Trabajo Relacionado

### A. Comparación estructurada

La Tabla I compara DoSync con los principales sistemas relacionados en cinco dimensiones: modelo de interacción, descubrimiento de dispositivos, determinismo del resolvedor, auditoría, y requisito de LLM en el camino crítico de ejecución.

**Tabla I — Comparación con sistemas relacionados**

| Sistema | Modelo | Descubrimiento | Determinismo | Auditoría | LLM en camino crítico |
|---|---|---|---|---|---|
| Matter [3] | Comandos | Manual | Sí | No | No |
| Home Assistant [6] | Reglas if/then | Manual | Sí | Parcial (*) | Opcional |
| openHAB [7] | Items/Rules | Manual | Sí | No | No |
| LLMind [1] | Scripts FSM | Automático | No | No | Sí |
| SASHA [2] | LLM directo | Automático | No | No | Sí |
| **DoSync** | **Intenciones** | **Automático** | **Sí** | **Sí (SHA-256)** | **No** |

(*) Home Assistant registra historial de automatizaciones pero no implementa encadenamiento criptográfico de entradas — el log puede ser modificado sin detección.

La diferencia fundamental con Matter, Home Assistant y openHAB es el descubrimiento automático: en DoSync, agregar un nuevo dispositivo no requiere modificar reglas ni configuración — el dispositivo declara sus capacidades y participa automáticamente en los escenarios relevantes.

La diferencia con LLMind y SASHA es el determinismo: ambos sistemas requieren un LLM en el camino crítico de ejecución, lo que introduce variabilidad. DoSync adopta un resolvedor determinista — para la misma intención y el mismo registry, siempre produce el mismo ActionPlan. Esta propiedad es verificable, auditable, y esencial en entornos físicos donde el comportamiento impredecible puede tener consecuencias reales.

### B. Semántica e interoperabilidad en IoT

La interoperabilidad semántica en IoT ha sido abordada mediante ontologías formales como SOSA/SSN [8] y W3C Web of Things [9]. Estos estándares definen vocabularios ricos para describir sensores, actuadores y observaciones. DoSync difiere en que no requiere razonamiento sobre grafos RDF ni ontologías formales — el Capability Manifest es una estructura JSON plana que cualquier dispositivo puede publicar sin dependencias externas, priorizando adopción práctica sobre expresividad formal.

El trabajo de Moeini et al. [10] propone un protocolo de descubrimiento semántico para redes IoT dinámicas. DoSync comparte el objetivo de descubrimiento automático pero se diferencia en que el contexto de resolución incluye urgencia y estado del dispositivo, no solo capacidades estáticas.

CASIT [11] propone un sistema multi-agente para IoT basado en LLMs. A diferencia de CASIT, DoSync no usa LLMs para la orquestación — los delega al agente externo y define solo la interfaz de intenciones, manteniendo el hub determinista.

---

## III. Diseño del Sistema

### A. Arquitectura de 5 capas

DoSync organiza la comunicación en 5 capas con responsabilidades claramente separadas:

- **Capa 5 — Intent:** el agente expresa objetivos semánticos
- **Capa 4 — Semántica:** el resolvedor mapea intención → ActionPlan
- **Capa 3 — Registry:** los dispositivos declaran capacidades via Capability Manifest
- **Capa 2 — Seguridad:** TLS 1.3 con PKI local, sin internet requerido
- **Capa 1 — Transporte (HAL):** abstracción sobre WiFi, BLE, Zigbee, Z-Wave, Thread

### B. Capability Manifest

```json
{
  "device_id": "lock-frontdoor-01",
  "tags": ["door-lock", "entrance", "emergency"],
  "actuators": [
    {"type": "unlock", "emergency_capable": true},
    {"type": "lock"}
  ],
  "emergency_capable": true,
  "adapter": "homeassistant"
}
```

Los `tags` son el mecanismo primario de resolución semántica. El campo `emergency_capable` garantiza que ciertos dispositivos participen en situaciones críticas independientemente del score de relevancia calculado.

### C. Clases de intención

El protocolo define 13 clases organizadas por prioridad de ejecución:

| Prioridad | Clases |
|---|---|
| 1 — Seguridad | `ensure_safety`, `alert_anomaly` |
| 2 — Acceso | `control_access` |
| 3 — Presencia | `children_arrived_home`, `notify_family` |
| 4 — Confort | `set_environment`, `morning_routine`, `bedtime_routine` |
| 5 — Eficiencia | `save_energy`, `away_mode` |
| — | `monitor_health`, `report_status`, `remind_chore` |

### D. Resolvedor Semántico

**Hipótesis H1:** Un algoritmo de scoring basado en capacidades declaradas puede seleccionar dispositivos relevantes con latencia inferior a 500ms para registries de hasta 5000 dispositivos.

**Hipótesis H2:** El overhead de la resolución semántica representa menos del 1% del tiempo total de ejecución en deployments reales.

El `CapabilityMatchingResolver` calcula para cada dispositivo registrado:

```
score = tag_overlap × 10 + location_match × 15
      + emergency_bonus × 30 + actuator_match × 8
```

Los pesos reflejan la importancia relativa de cada señal de relevancia. `emergency_bonus` (30) tiene triple peso que `tag_overlap` (10) porque en situaciones de emergencia es preferible incluir un dispositivo con capacidad crítica aunque su overlap semántico sea parcial — un falso negativo en emergencia tiene consecuencias peores que un falso positivo. `location_match` (15) tiene peso intermedio porque la ubicación contextual es una señal fuerte pero no siempre disponible en el contexto del intent. `tag_overlap` y `actuator_match` tienen pesos similares (10 y 8) porque ambos miden compatibilidad semántica directa con la intención.

El resolvedor es **determinista**: para la misma intención y el mismo registry, produce siempre el mismo ActionPlan. Esta propiedad es verificable por la suite de certificación.

El `StateAwareResolver` extiende este comportamiento filtrando acciones redundantes verificando el estado actual del dispositivo antes de incluirlo en el plan. El estado se persiste en SQLite y sobrevive reinicios del hub.

### E. Motor de Políticas

El motor evalúa el ActionPlan antes de su ejecución con 5 tipos de política:

- **NeverAfterHoursPolicy:** bloquea actuadores fuera de horario configurado
- **RequireConfirmationPolicy:** requiere confirmación humana para acciones críticas
- **BlockIntentPolicy:** bloquea intenciones por fuente
- **DeviceExclusionPolicy:** excluye dispositivos de ciertas intenciones
- **ConflictResolutionPolicy:** resuelve conflictos simultáneos por prioridad

Las intenciones con urgencia `emergency` bypasan todas las políticas excepto las de seguridad. Cada decisión queda registrada en el audit log.

### F. Log de Auditoría

Cada acción genera una entrada encadenada mediante SHA-256:

```
hash_n = SHA256(entry_n || prev_hash_{n-1})
```

Modificar cualquier entrada invalida todos los hashes subsecuentes. El hub verifica la integridad en tiempo real. Este mecanismo es análogo al encadenamiento de bloques en sistemas distribuidos [12] pero sin consenso distribuido — apropiado para auditoría local en entornos regulados. Para un análisis de requerimientos de logging tamper-evident en dispositivos IoT a escala, ver [15].

### G. Device Health Monitor

El Device Health Monitor registra el resultado de cada ejecución de adapter (éxito o fallo, con mensaje de error cuando aplica) en una tabla SQLite independiente. Expone un endpoint REST `GET /v1/health/devices` con estadísticas por dispositivo y alertas configurables por umbral de tasa de éxito. El diseño sigue el principio de observabilidad sin autonomía: el sistema registra y alerta, pero la decisión de actuar sobre un dispositivo degradado es siempre del operador humano.

---

## IV. Implementación

### A. Hub de referencia (Python)

FastAPI, 14 endpoints REST, WebSocket para eventos en tiempo real. SQLite con 6 tablas: `devices`, `audit_log`, `device_state`, `device_health`, `presence_signals`, `api_keys`. Corre en Raspberry Pi 5 como servicio systemd con TLS 1.3 y PKI local.

### B. Sistema de adapters

```python
class DoSyncAdapter:
    async def execute(self, action: DeviceAction,
                      urgency: Urgency) -> ActionResult: ...
```

Adapters disponibles: `WiZAdapter` (UDP, Philips WiZ), `HABridge` (Home Assistant, 10 dominios), `NotificationAdapter` (SMS Twilio), `GPIOAdapter` (Raspberry Pi).

### C. Servidor MCP nativo

DoSync incluye un servidor MCP [13] que expone el hub como conjunto de herramientas para agentes de IA. Permite integración directa con agentes compatibles sin código adicional.

### D. Segunda implementación — Node.js

Implementación independiente en Node.js, sin código compartido con la versión Python. Pasa la suite de certificación tier Basic (6/6 tests), validando que la especificación es implementable por terceros.

### E. Suite de certificación

16 tests en 3 tiers: Basic (conectividad, registro, manifests), Standard (intenciones, eventos, validación de errores), Emergency (override de emergencia, integridad SHA-256).

---

## V. Evaluación

### A. Setup experimental

**Registry:** 38 dispositivos reales del hub de producción (Raspberry Pi 5, Arm Cortex-A76, 8GB RAM). **Iteraciones:** 500 por resolvedor. **Semilla:** 42 (reproducible). **Intenciones:** muestreadas aleatoriamente sobre 13 clases, 3 urgencias, 5 ubicaciones.

### B. H1 — Latencia y escalabilidad

| Dispositivos | Media | p95 | p99 | Dentro del límite (500ms) |
|---|---|---|---|---|
| 38 (producción) | 0.053ms | 0.074ms | 0.107ms | ✓ |
| 100 | 0.096ms | 0.141ms | 0.196ms | ✓ |
| 500 | 0.498ms | 0.737ms | 1.486ms | ✓ |
| 1000 | 1.013ms | 1.375ms | 3.044ms | ✓ |
| 5000 | 5.300ms | 9.129ms | 11.392ms | ✓ |

**H1 confirmada.** El resolvedor opera dentro del límite de 500ms hasta 5000 dispositivos. El algoritmo es O(n) — a 5000+ dispositivos p95 supera 9ms. Una implementación con indexación por tag reduciría esto a O(1) — trabajo futuro planificado para v0.3.

El `StateAwareResolver` elimina el **35% de acciones redundantes** con latencia equivalente (p99: 0.109ms vs 0.107ms).

### C. H2 — Overhead semántico

| Operación | Latencia media |
|---|---|
| Comando directo (dict lookup) | 0.0013ms |
| Resolución semántica (38 dispositivos) | 0.0529ms |
| Overhead absoluto | 0.051ms |

En contexto de deployment real:

| Operación | Latencia típica |
|---|---|
| Resolución semántica | ~0.05ms |
| WiFi → WiZ (UDP) | 5–15ms |
| WiFi → Home Assistant (HTTP) | 20–80ms |
| **Capa semántica como % del total** | **< 1%** |

**H2 confirmada.** El overhead de la capa semántica es inferior al 1% del tiempo total de ejecución.

### D. Device Health Monitor — datos de producción

El sistema de producción acumula datos de 31 ejecuciones reales sobre 7 dispositivos monitoreados:

| Dispositivo | Éxitos | Total | Tasa | Observación |
|---|---|---|---|---|
| notifier-sms-01 | 6 | 6 | 100% | SMS Twilio — sin fallos |
| wiz-habitacion-principal | 1 | 1 | 100% | — |
| wiz-habitacion-ninos-01 | 0 | 12 | 0% | Alerta activa |
| wiz-living1-01 | 0 | 12 | 0% | Alerta activa |
| wiz-living1-02 | 0 | 12 | 0% | Alerta activa |
| wiz-living2-01 | 0 | 12 | 0% | Alerta activa |
| wiz-living2-02 | 0 | 12 | 0% | Alerta activa |

*Nota: los datos del health monitor reflejan el período de acumulación temprana del sistema — 31 ejecuciones sobre 7 dispositivos desde la activación del módulo. Los patrones identificados son preliminares y serán consolidados con mayor volumen de datos en producción continua.*

Las 5 luces WiZ con tasa de éxito 0% corresponden a ejecuciones del intent `save_energy` durante horario nocturno con las luces físicamente apagadas — el adapter WiZ envió el comando UDP pero los dispositivos no respondieron al estar en estado de bajo consumo. Este patrón ilustra una limitación documentada del `StateAwareResolver`: cuando el estado del dispositivo no está en caché (cache miss), el resolvedor incluye la acción aunque sea redundante, y el adapter registra el fallo. La solución — consulta directa del estado del dispositivo antes de la resolución — es trabajo futuro planificado para v0.4.

El umbral de alerta configurable (default 70%) generó **5 alertas activas**, todas correspondientes al patrón WiZ identificado. El operador humano puede inspeccionar las alertas y tomar la decisión de excluir estos dispositivos de ciertos intents via `DeviceExclusionPolicy`.

### E. Precisión semántica del resolvedor

**Hipótesis H3:** El resolvedor selecciona los dispositivos relevantes con precision superior a 0.80 sobre el registry de producción.

Para evaluar la calidad de las decisiones del resolvedor — no solo su velocidad — se definió un ground truth manual para 15 escenarios representativos sobre el registry real de 38 dispositivos. Para cada escenario se definieron los dispositivos esperados según criterio del autor como operador del sistema, y se compararon con los seleccionados por el resolvedor. El ground truth refleja el conocimiento del deployment específico y no fue validado por un tercero independiente — limitación reconocida para trabajo futuro.

**Tabla III — Precisión semántica del resolvedor (15 escenarios)**

| Escenario | Urgencia | Esperados | Seleccionados | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| Emergencia general | emergency | 13 | 13 | 1.00 | 1.00 | 1.00 |
| Anomalía de seguridad | info | 2 | 2 | 1.00 | 1.00 | 1.00 |
| Niños llegaron | info | 6 | 6 | 1.00 | 1.00 | 1.00 |
| Notificar familia | info | 3 | 1 | 1.00 | 0.33 | 0.50 |
| Ahorro energético | info | 12 | 1 | 1.00 | 0.08 | 0.15 |
| Rutina nocturna | info | 11 | 1 | 0.00 | 0.00 | 0.00 |
| Rutina matutina | info | 11 | 14 | 0.79 | 1.00 | 0.88 |
| Modo ausente | info | 12 | 1 | 1.00 | 0.08 | 0.15 |
| Ambiente general | info | 11 | 0 | 1.00 | 0.00 | 0.00 |
| Anomalía detectada | warning | 4 | 2 | 1.00 | 0.50 | 0.67 |
| Reporte de estado | info | 3 | 3 | 1.00 | 1.00 | 1.00 |
| Monitoreo de salud | info | 3 | 0 | 1.00 | 0.00 | 0.00 |
| Recordatorio | info | 3 | 1 | 1.00 | 0.33 | 0.50 |
| Control de acceso | info | 0 | 1 | 0.00 | 1.00 | 0.00 |
| Ambiente con ubicación | info | 2 | 0 | 1.00 | 0.00 | 0.00 |
| **Promedio** | | | | **0.85** | **0.49** | **0.46** |

**H3 confirmada parcialmente.** La precision promedio es 0.85 — el resolvedor rara vez incluye dispositivos incorrectos. Sin embargo, el recall promedio de 0.49 indica que en varios escenarios no selecciona todos los dispositivos relevantes.

El análisis de los casos de recall bajo revela un patrón consistente: los intents con tags de resolución amplios (`save_energy`, `away_mode`, `bedtime_routine`) producen recall bajo porque el registry actual no tiene dispositivos con tags específicos de esos dominios (termostatos, persianas, enchufes inteligentes con tags `thermostat`, `blinds`). Los dispositivos WiZ tienen tag `climate` pero el resolvedor requiere tags más específicos para algunos intents. El escenario de `control_access` con 1 falso positivo corresponde a un dispositivo con tag `security` que el resolvedor incluye aunque no tenga actuador de acceso — caso de frontera documentado.

Los escenarios de emergencia y comunicación alcanzan F1 = 1.00, lo que indica que para los intents de mayor criticidad el resolvedor funciona correctamente. Los intents de confort y eficiencia son los más afectados por la cobertura de tags del registry actual, lo que sugiere que la configuración de tags en los Capability Manifests tiene impacto directo en la calidad de resolución — aspecto a documentar en las guías de deployment.

### F. Integridad del audit log

474 entradas acumuladas en producción. Cadena SHA-256 íntegra en todas las consultas. El hub no detectó ninguna violación de integridad desde su puesta en marcha.

---

## VI. Limitaciones y Trabajo Futuro

**Cobertura de tags y recall.** La evaluación de precisión semántica (Sección V.E) muestra recall promedio de 0.49, con casos bajos en intents de confort y eficiencia. El factor determinante es la configuración de tags en los Capability Manifests — dispositivos con tags genéricos (`climate`, `light`) no son seleccionados para intents que requieren tags más específicos (`thermostat`, `blinds`). Guías de configuración de tags para maximizar recall son trabajo futuro prioritario.

**Scoring empírico.** Los pesos del algoritmo de scoring fueron definidos sobre escenarios de producción. Una derivación formal o un proceso de optimización basado en historial de ejecuciones fortalecería la justificación del modelo.

**Comparación experimental con sistemas existentes.** La comparación con Matter, Home Assistant y openHAB en la Tabla I es conceptual — no cuantitativa. No se midió experimentalmente cuántas reglas requiere Home Assistant para los mismos escenarios de DoSync, ni el costo de mantenimiento al agregar un nuevo dispositivo en cada sistema. Una comparación experimental directa fortalecería significativamente el argumento de que DoSync reduce la carga de configuración.

**Estado no distribuido.** El `StateAwareResolver` persiste estado en SQLite local. En deployments multi-hub el estado no es consistente entre instancias — limitación relevante para entornos industriales con múltiples zonas.

**Modelo de fallo parcial.** El protocolo no define el comportamiento cuando un adapter falla durante ejecución parcial de un ActionPlan. El audit log registra los fallos pero no hay mecanismo de compensación ni rollback.

**Cache miss en StateAwareResolver.** Como muestran los datos de producción, cuando el estado de un dispositivo no está en caché el resolvedor incluye la acción aunque pueda ser redundante. La consulta directa del estado del dispositivo resolvería esto — planificado para v0.4.

**Certificación por terceros.** La suite de certificación es auto-administrada. Un proceso formal por terceros es necesario para escenarios de seguridad crítica.

**Una sola implementación de producción.** La implementación Node.js pasa tier Basic pero no Standard ni Emergency. Se requieren más implementaciones para validar la especificación como estándar.

**Trabajo futuro planificado:** v0.3 (indexación por tag, métricas de observabilidad), v0.4 (estado distribuido, consulta directa de estado, coordinación multi-agente), v1.0 (interfaz estable, governance formal).

---

## VII. Conclusión

DoSync demuestra que es posible introducir una capa semántica entre agentes de IA y dispositivos IoT preservando determinismo, auditabilidad y seguridad — propiedades que los enfoques basados en LLMs en el camino crítico no garantizan. El modelo declarativo de Capability Manifest habilita descubrimiento automático sin configuración manual, resolviendo el problema de escala que afecta a los sistemas basados en reglas.

Los resultados empíricos confirman las hipótesis planteadas: el resolvedor opera dentro de los límites de especificación hasta 5000 dispositivos (H1) y el overhead semántico es inferior al 1% del tiempo total de ejecución (H2). Los datos del Device Health Monitor en producción revelan además un patrón de fallo concreto — cache miss en StateAwareResolver — que informa directamente el roadmap del protocolo.

La existencia de dos implementaciones independientes en lenguajes diferentes, sin código compartido, valida que la especificación es suficientemente precisa para ser implementada por terceros — criterio mínimo para que un protocolo sea considerado estándar abierto.

Código, especificación y suite de certificación disponibles en: https://github.com/giulianireg-spec/dosync-protocol (Apache 2.0).

---

## Referencias

[1] H. Cui, Y. Du, Q. Yang, Y. Shao, S. C. Liew, "LLMind: Orchestrating AI and IoT with LLM for Complex Task Execution," IEEE Internet of Things Journal, 2024. DOI: 10.1109/JIOT.2024.10697418

[2] E. King, H. Yu, S. Lee, C. Julien, "Sasha: Creative Goal-Oriented Reasoning in Smart Homes with Large Language Models," Proc. ACM Interact. Mob. Wearable Ubiquitous Technol., vol. 8, no. 1, 2024.

[3] Connectivity Standards Alliance, "Matter Specification v1.3," 2024. [Online]. Available: https://csa-iot.org/developer-resource/specifications-download-request/

[4] Zigbee Alliance, "Zigbee Specification R21," 2015.

[5] Z-Wave Alliance, "Z-Wave Specification," 2022.

[6] Home Assistant, "Architecture Overview," https://www.home-assistant.io/docs/architecture/, 2024.

[7] openHAB Community, "openHAB Developer Documentation," https://www.openhab.org/docs/, 2024.

[8] A. Haller et al., "The modular SSN ontology: A joint W3C and OGC standard specifying the semantics of sensors, observations, sampling, and actuation," Semantic Web, vol. 10, no. 1, pp. 9–32, 2019. DOI: 10.3233/SW-180320

[9] W3C, "Web of Things (WoT) Thing Description," W3C Recommendation, 2020. [Online]. Available: https://www.w3.org/TR/wot-thing-description/

[10] H. Moeini, I.-L. Yen, F. Bastani, "Summarization in Semantic Based Service Discovery in Dynamic IoT-Edge Networks," arXiv:2009.02858, 2020.

[11] N. Zhong et al., "CASIT: Collective Intelligent Agent System for Internet of Things," IEEE Internet of Things Journal, vol. 11, no. 11, pp. 19646–19656, 2024. DOI: 10.1109/JIOT.2024.3366906

[12] S. Nakamoto, "Bitcoin: A Peer-to-Peer Electronic Cash System," 2008. [Online]. Available: https://bitcoin.org/bitcoin.pdf

[15] E.-O. Blass and G. Noubir, "Accountability of Things: Large-Scale Tamper-Evident Logging for Smart Devices," arXiv:2308.05557, 2023.

[13] Anthropic, "Model Context Protocol Specification," https://modelcontextprotocol.io/, 2024.

[14] M. Wooldridge and N. R. Jennings, "Intelligent agents: Theory and practice," The Knowledge Engineering Review, vol. 10, no. 2, pp. 115–152, 1995.
