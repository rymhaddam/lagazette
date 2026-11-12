"""DAG quotidien de La gazette : collecte des flux RSS à 5 h (heure de Paris), puis
nettoyage, contrôles qualité et rétention (TD 4).

Aucune logique métier ici : le DAG appelle les fonctions du paquet `gazette`,
qui fonctionnent aussi sans Airflow (`python -m gazette collect`).
"""

from __future__ import annotations

from datetime import date, timedelta

import pendulum
from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from airflow.timetables.trigger import CronTriggerTimetable

PARIS = pendulum.timezone("Europe/Paris")


@dag(
    dag_id="gazette_daily",
    description="Collecte RSS PQR/PQN, stockage brut, nettoyage et contrôles qualité",
    schedule=CronTriggerTimetable("0 5 * * *", timezone=PARIS),
    start_date=pendulum.datetime(2026, 10, 1, tz=PARIS),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "la-gazette"},
    tags=["gazette", "collecte", "qualite"],
)
def gazette_daily():
    @task
    def collection_date() -> str:
        """Jour de collecte = date (heure de Paris) à laquelle le run est prévu."""
        run_after = get_current_context()["dag_run"].run_after
        return pendulum.instance(run_after).in_timezone(PARIS).date().isoformat()

    @task
    def list_sources() -> list[str]:
        from gazette.config import load_settings
        from gazette.sources import load_sources

        return [source.slug for source in load_sources(load_settings().sources_file)]

    @task(
        retries=2,
        retry_delay=timedelta(minutes=2),
        retry_exponential_backoff=True,
        map_index_template="{{ source_slug }}",
    )
    def collect(slug: str, day: str) -> dict:
        from gazette.collect.fetcher import PoliteFetcher
        from gazette.collect.runner import collect_source
        from gazette.config import load_settings
        from gazette.sources import get_source, load_sources

        get_current_context()["source_slug"] = slug  # libellé de la tâche mappée dans l'UI
        settings = load_settings()
        source = get_source(load_sources(settings.sources_file), slug)
        with PoliteFetcher(settings) as fetcher:
            result = collect_source(source, settings, date.fromisoformat(day), fetcher)
        if result.status == "failed":
            # robots.txt interdit, 404… : inutile de relancer.
            if not result.retryable:
                raise AirflowFailException(result.error)
            raise RuntimeError(result.error)
        return result.model_dump()

    @task(trigger_rule="all_done")
    def write_manifest(day: str) -> dict:
        """S'exécute même si des sources ont échoué : elles apparaissent en échec."""
        from gazette.collect.runner import finalize
        from gazette.config import load_settings

        return finalize(load_settings(), date.fromisoformat(day))["summary"]

    @task
    def clean(day: str) -> dict:
        """Brut du jour -> articles_propres / articles_rejetes + rapport de qualité."""
        from gazette.config import load_settings
        from gazette.quality.runner import run_clean

        report, _ = run_clean(load_settings(), date.fromisoformat(day))
        # Résumé en XCom : visible dans l'interface Airflow à chaque passage.
        return {
            "entrees": report["entrees"],
            "gardes": report["gardes"],
            "rejetes": report["rejetes"],
            "par_regle": {k: v["nombre"] for k, v in report["par_regle"].items()},
            "stockage": report["stockage"],
        }

    @task
    def quality_checks(day: str) -> dict:
        """Faits vérifiés sur les données réelles ; métriques en base ; alerte Discord si
        un fait est faux. La tâche réussit quand même : l'alerte est le signal, et la
        publication du jour n'est pas bloquée par un seuil."""
        from gazette.config import load_settings
        from gazette.quality.runner import run_quality_checks

        return run_quality_checks(load_settings(), date.fromisoformat(day))

    @task(trigger_rule="all_done")
    def retention() -> int:
        """Archive en stockage froid ce qui a plus de 30 jours, supprime le brut de 90 jours."""
        from gazette.config import load_settings
        from gazette.storage import retention as policy

        settings = load_settings()
        return len(policy.apply(settings, settings.today()))

    day = collection_date()
    collected = collect.partial(day=day).expand(slug=list_sources())
    manifest = write_manifest(day)
    collected >> manifest
    manifest >> clean(day) >> quality_checks(day) >> retention()


gazette_daily()
