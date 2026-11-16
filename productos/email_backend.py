import resend
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

class ResendEmailBackend(BaseEmailBackend):
    """
    Backend personalizado para que Django envíe correos usando la API de Resend
    sin usar los puertos SMTP bloqueados por Render.
    """
    def send_messages(self, email_messages):
        resend.api_key = getattr(settings, 'RESEND_API_KEY', '')
        count = 0
        for message in email_messages:
            try:
                params = {
                    "from": message.from_email or "JoyasApp <onboarding@resend.dev>",
                    "to": message.to,
                    "subject": message.subject,
                    "html": message.body, # O message.alternatives si envías HTML enriquecido
                }
                resend.Emails.send(params)
                count += 1
            except Exception as e:
                if not self.fail_silently:
                    raise e
        return count