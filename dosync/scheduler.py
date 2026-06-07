"""
DoSync Scheduler
Dispara rutinas familiares basadas en tiempo o condiciones.

Dos modos:
  - Tiempo real: verifica cada minuto si es hora de una rutina
  - Tiempo simulado: para demos y testing, acepta una hora ficticia
"""

from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .hub import DoSyncHub

from .models import FamilyProfile, Intent, IntentClass, RoutineAction, Urgency

log = logging.getLogger("dosync.scheduler")


# ── Trigger types ─────────────────────────────────────────────────────────────

@dataclass
class ScheduledTrigger:
    """Una regla que dispara un intent a una hora determinada."""
    name: str
    hour: int
    minute: int
    intent_class: IntentClass
    urgency: Urgency = Urgency.INFO
    context_builder: Optional[Callable] = None   # funcion que genera el context
    last_fired_date: Optional[str] = None        # "YYYY-MM-DD" — evita doble disparo


# ── Scheduler ─────────────────────────────────────────────────────────────────

class DoSyncScheduler:
    """
    Scheduler liviano para rutinas familiares.

    Uso tipico:
        scheduler = DoSyncScheduler(hub)
        scheduler.load_profile(family_profile)
        asyncio.create_task(scheduler.run())

    Para demos sin esperar la hora real:
        scheduler.simulate_time(21, 30)   # simula las 21:30
    """

    def __init__(self, hub: "DoSyncHub"):
        self.hub = hub
        self._triggers: list[ScheduledTrigger] = []
        self._simulated_time: Optional[tuple[int, int]] = None
        self._running = False
        self._morning_fired_today: Optional[str] = None

    # ── Carga de perfil ───────────────────────────────────────────────────────

    def load_profile(self, profile: FamilyProfile) -> None:
        """Carga las rutinas del perfil familiar como triggers."""
        self._profile = profile
        self._triggers.clear()

        # Rutina de hora de dormir
        if profile.routine_bedtime:
            self._triggers.append(ScheduledTrigger(
                name="bedtime",
                hour=profile.bedtime_hour,
                minute=profile.bedtime_minute,
                intent_class=IntentClass.BEDTIME_ROUTINE,
                urgency=Urgency.INFO,
                context_builder=lambda: {
                    "trigger":     "scheduled_bedtime",
                    "family":      profile.family_name,
                    "actions":     [
                        {"tag": a.tag, "action_type": a.action_type, "params": a.params}
                        for a in profile.routine_bedtime
                    ],
                    "message":     f"Rutina de noche activada para {profile.family_name}.",
                },
            ))

        log.info(
            "Profile loaded for '%s' — %d trigger(s): %s",
            profile.family_name,
            len(self._triggers),
            [t.name for t in self._triggers],
        )

    # ── Disparo manual de rutinas ─────────────────────────────────────────────

    async def fire_morning_routine(self, trigger: str = "first_motion") -> None:
        """
        Dispara la rutina de buenos dias.
        Llamado cuando el hub detecta primer movimiento del dia.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if self._morning_fired_today == today:
            log.info("Morning routine already fired today, skipping")
            return

        self._morning_fired_today = today
        profile = getattr(self, "_profile", None)
        if not profile or not profile.routine_morning:
            log.warning("No morning routine configured")
            return

        intent = Intent(
            intent=IntentClass.MORNING_ROUTINE,
            urgency=Urgency.INFO,
            source="scheduler",
            context={
                "trigger": trigger,
                "family":  profile.family_name,
                "actions": [
                    {"tag": a.tag, "action_type": a.action_type, "params": a.params}
                    for a in profile.routine_morning
                ],
                "message": f"Buenos dias, {profile.family_name}.",
            },
        )
        log.info("Firing morning routine for '%s'", profile.family_name)
        from .executor import SimulatedExecutor
        await self.hub.execute_intent(intent, SimulatedExecutor())

    async def fire_away_mode(self, trigger: str = "garage_opened") -> None:
        """
        Dispara el modo ausente.
        Llamado cuando el hub detecta que todos salieron.
        """
        profile = getattr(self, "_profile", None)
        if not profile or not profile.routine_away:
            log.warning("No away routine configured")
            return

        intent = Intent(
            intent=IntentClass.AWAY_MODE,
            urgency=Urgency.INFO,
            source="scheduler",
            context={
                "trigger": trigger,
                "family":  profile.family_name,
                "actions": [
                    {"tag": a.tag, "action_type": a.action_type, "params": a.params}
                    for a in profile.routine_away
                ],
                "message": "Away mode activated.",
            },
        )
        log.info("Firing away mode for '%s'", profile.family_name)
        from .executor import SimulatedExecutor
        await self.hub.execute_intent(intent, SimulatedExecutor())

    # ── Tiempo simulado (para demos) ──────────────────────────────────────────

    def simulate_time(self, hour: int, minute: int) -> None:
        """
        Fuerza una hora simulada para la proxima verificacion.
        Util para demos sin tener que esperar la hora real.
        """
        self._simulated_time = (hour, minute)
        log.info("Simulated time set to %02d:%02d", hour, minute)

    def _current_time(self) -> tuple[int, int]:
        if self._simulated_time:
            t = self._simulated_time
            self._simulated_time = None   # se consume una sola vez
            return t
        now = datetime.now()
        return now.hour, now.minute

    # ── Loop principal ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """
        Loop principal del scheduler.
        Verifica cada 60 segundos si algun trigger debe dispararse.
        """
        self._running = True
        log.info("Scheduler started")
        from .executor import SimulatedExecutor
        executor = SimulatedExecutor()

        while self._running:
            hour, minute = self._current_time()
            today = datetime.now().strftime("%Y-%m-%d")

            for trigger in self._triggers:
                if (trigger.hour == hour and
                    trigger.minute == minute and
                    trigger.last_fired_date != today):

                    trigger.last_fired_date = today
                    context = trigger.context_builder() if trigger.context_builder else {}
                    intent = Intent(
                        intent=trigger.intent_class,
                        urgency=trigger.urgency,
                        source="scheduler",
                        context=context,
                    )
                    log.info(
                        "Scheduler firing '%s' at %02d:%02d",
                        trigger.name, hour, minute,
                    )
                    await self.hub.execute_intent(intent, executor)

            await asyncio.sleep(60)

    def stop(self) -> None:
        self._running = False
        log.info("Scheduler stopped")
