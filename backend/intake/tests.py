from unittest.mock import Mock, patch
from django.test import SimpleTestCase, override_settings
from .providers import GatewayProvider, DeliveryFailure, digest


@override_settings(WHATSAPP_GATEWAY_URL='http://127.0.0.1:3001',
                   WHATSAPP_GATEWAY_KEY='test-key',
                   WHATSAPP_GATEWAY_SESSION='desk session')
class GatewayContractTests(SimpleTestCase):
    @patch('intake.providers.requests.post')
    def test_send_matches_cloned_gateway_contract(self, post):
        post.return_value.ok = True
        post.return_value.json.return_value = {'status': True, 'message': 'Message sent successfully'}
        delivery = Mock()
        self.assertEqual(GatewayProvider().send('+9779801234599', '123456', delivery), '')
        url = post.call_args.args[0]
        options = post.call_args.kwargs
        self.assertEqual(url, 'http://127.0.0.1:3001/api/messages/desk%20session/9779801234599%40s.whatsapp.net/send')
        self.assertEqual(options['headers'], {'X-API-Key': 'test-key'})
        self.assertIn('123456', options['json']['message']['text'])
        self.assertEqual(delivery.content_hash, digest(options['json']['message']['text']))
        self.assertFalse(options['allow_redirects'])
        delivery.save.assert_called_once_with(update_fields=['content_hash'])

    @patch('intake.providers.requests.post')
    def test_rejected_send_has_fixed_error_without_response_content(self, post):
        post.return_value.ok = False
        with self.assertRaisesRegex(DeliveryFailure, '^GATEWAY_SEND_FAILED$'):
            GatewayProvider().send('+9779801234599', '123456', Mock())

    @override_settings(WHATSAPP_GATEWAY_KEY='')
    @patch('intake.providers.requests.post')
    def test_missing_key_does_not_make_network_request(self, post):
        with self.assertRaisesRegex(DeliveryFailure, '^PROVIDER_NOT_CONFIGURED$'):
            GatewayProvider().send('+9779801234599', '123456', Mock())
        post.assert_not_called()
