import resend
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


class ResendEmailBackend(BaseEmailBackend):
    """Backend personalizado para que Django envíe correos usando la API de Resend

    sin usar los puertos SMTP bloqueados por Render.
    """

    def send_messages(self, email_messages):
        resend.api_key = getattr(settings, 'RESEND_API_KEY', '')
        count = 0
        for message in email_messages:
            try:
                # Extraer contenido HTML enriquecido si Django lo envía en alternatives
                html_content = message.body
                if hasattr(message, 'alternatives'):
                    for content, mimetype in message.alternatives:
                        if mimetype == 'text/html':
                            html_content = content
                            break

                params = {
                    "from": (
                        message.from_email
                        or "Soporte JoyasApp <soporte@joyasapp.com>"
                    ),
                    "to": message.to,
                    "reply_to": "joyasapp79@gmail.com",
                    "subject": message.subject,
                    "html": html_content,
                }
                resend.Emails.send(params)
                count += 1
            except Exception as e:
                if not self.fail_silently:
                    raise e
        return count