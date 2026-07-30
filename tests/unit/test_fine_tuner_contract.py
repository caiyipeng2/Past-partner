import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from src.training.fine_tuner import FineTuner, TrainingCapabilityError


class FineTunerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.output_dir = Path.cwd() / ".test-runtime" / str(uuid4())
        self.config = SimpleNamespace(output_dir=str(self.output_dir))

    def tearDown(self) -> None:
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_train_never_returns_fabricated_success(self) -> None:
        tuner = FineTuner(self.config)

        with self.assertRaises(TrainingCapabilityError) as captured:
            tuner.train()

        self.assertEqual("capability_not_configured", captured.exception.code)
        self.assertFalse(self.output_dir.exists())

    def test_evaluate_requires_a_real_trainer_and_dataset(self) -> None:
        tuner = FineTuner(self.config)

        with self.assertRaises(TrainingCapabilityError) as captured:
            tuner.evaluate()
        self.assertEqual("capability_not_configured", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
