from django import template
from django.urls import reverse

register = template.Library()

@register.simple_tag(takes_context=True)
def tenant_url(context, client, view_name, *args):
    request = context['request']
    domain = client.domains.filter(is_primary=True).first()
    
    if not domain:
        return "#"
        
    path = reverse(view_name, args=args)
    
    # Safely handle the port number ONLY if it is needed (like localhost:8000)
    port = request.META.get('SERVER_PORT')
    if port and port not in ['80', '443']:
        return f"{request.scheme}://{domain.domain}:{port}{path}"
        
    # Production URL without ugly ports
    return f"{request.scheme}://{domain.domain}{path}"