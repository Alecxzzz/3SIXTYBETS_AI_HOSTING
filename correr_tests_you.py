"""Corre los tests de integracion de You.com y muestra el fallo completo."""
import io
import unittest

if __name__ == "__main__":
    from tests.test_you_integration import YouIntegrationTests  # noqa

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(YouIntegrationTests)
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=2)
    result = runner.run(suite)
    for test, traceback in getattr(result, "failures", []) + getattr(result, "errors", []):
        print("=" * 70)
        print("FALLO:", test)
        print(traceback)
    print("tests:", result.testsRun, "fallos:", len(result.failures), "errores:", len(result.errors))
