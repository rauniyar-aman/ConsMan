from unittest.mock import patch
from subprocess import CalledProcessError
from django.test import SimpleTestCase
from deployment.start_free import main


class FreeServiceStartupTests(SimpleTestCase):
    @patch('deployment.start_free.os.execvp')
    @patch('deployment.start_free.os.chdir')
    @patch('deployment.start_free.subprocess.run')
    def test_release_precedes_server(self, release, chdir, server):
        main()
        self.assertTrue(release.call_args.kwargs['check'])
        self.assertEqual(server.call_args.args[0], 'gunicorn')
        self.assertIn('config.wsgi:application', server.call_args.args[1])

    @patch('deployment.start_free.os.execvp')
    @patch('deployment.start_free.subprocess.run', side_effect=CalledProcessError(1, 'release'))
    def test_failed_release_does_not_start_server(self, release, server):
        with self.assertRaises(CalledProcessError):
            main()
        server.assert_not_called()
