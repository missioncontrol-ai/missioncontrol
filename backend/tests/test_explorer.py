import unittest

from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Kluster, Mission, Task
from app.routers.explorer import get_explorer_node, get_explorer_tree


class ExplorerRouterTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            mission = Mission(
                id="mhash123abc",
                name="Mission Alpha",
                owners="owner@example.com",
            )
            kluster = Kluster(
                id="chash123abc",
                mission_id=mission.id,
                name="Kluster One",
                tags="alpha,platform",
            )
            task_a = Task(
                kluster_id=kluster.id,
                title="Design explorer tree",
                status="in_progress",
                owner="owner@example.com",
            )
            task_b = Task(
                kluster_id=kluster.id,
                title="Write smoke checks",
                status="proposed",
            )
            session.add(mission)
            session.add(kluster)
            session.add(task_a)
            session.add(task_b)
            session.commit()
            session.refresh(task_a)
            self.task_id = task_a.id
            self.mission_id = mission.id
            self.kluster_id = kluster.id

    def test_tree_returns_mission_cluster_and_tasks(self):
        tree = get_explorer_tree(limit_tasks_per_cluster=5)
        self.assertEqual(tree.mission_count, 1)
        self.assertEqual(tree.kluster_count, 1)
        self.assertEqual(tree.task_count, 2)
        self.assertEqual(tree.missions[0].id, self.mission_id)
        self.assertEqual(tree.missions[0].klusters[0].id, self.kluster_id)

    def test_tree_search_filter(self):
        tree = get_explorer_tree(q="smoke", limit_tasks_per_cluster=5)
        self.assertEqual(tree.mission_count, 1)
        self.assertEqual(tree.task_count, 1)
        self.assertEqual(tree.missions[0].klusters[0].recent_tasks[0].title, "Write smoke checks")

    def test_node_detail_for_task(self):
        detail = get_explorer_node("task", str(self.task_id), limit_tasks=50)
        self.assertEqual(detail.task.id, self.task_id)
        self.assertEqual(detail.kluster.id, self.kluster_id)
        self.assertEqual(detail.mission.id, self.mission_id)


if __name__ == "__main__":
    unittest.main()
