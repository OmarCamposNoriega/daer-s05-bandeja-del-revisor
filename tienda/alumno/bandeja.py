"""PARTES 2 y 3b — La bandeja del revisor: el ModelAdmin de Sugerencia.

Lo que ya está hecho te sirve de modelo: la columna «actual», la acción «Rechazar» (mírala
antes de escribir «Aprobar y aplicar») y la regla de que las sugerencias no se borran.

Comprueba tu avance con: python manage.py duelo
"""

from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone
from django.utils.text import Truncator

from tienda.grupos import es_agente
from tienda.models import Sugerencia

from .verificador import verificar


class SugerenciaAdmin(admin.ModelAdmin):
    list_display = (
        'producto', 'campo', 'valor_actual_col', 'valor_propuesto',
        'verificacion', 'motivo_corto', 'origen', 'estado', 'creado_por',
    )
    list_filter = ('estado', 'origen', 'campo')
    search_fields = ('producto__nombre', 'motivo')
    list_select_related = ('producto', 'creado_por')
    actions = ['aprobar_y_aplicar', 'rechazar']

    def get_readonly_fields(self, request, obj=None):
        base = ('origen', 'estado', 'creado_por', 'decidido_por', 'decidido_en', 'valor_anterior')
        if obj is not None:
            return ('producto', 'campo', 'valor_propuesto', 'motivo') + base
        return base

    # Las sugerencias son la bitácora de lo que propuso la IA y de quién lo decidió: no se borran.
    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.creado_por = request.user
            obj.origen = 'ia' if es_agente(request.user) else 'persona'
        super().save_model(request, obj, form, change)

    def has_aprobar_permission(self, request):
        return request.user.has_perm('tienda.aprobar_sugerencia')

    @admin.display(description='actual')
    def valor_actual_col(self, obj):
        return obj.valor_actual()

    @admin.display(description='verificador')
    def verificacion(self, obj):
        if obj.estado != Sugerencia.PENDIENTE:
            return '—'
        problemas = verificar(obj)
        if not problemas:
            return '✔ pasa'
        return '✘ ' + '; '.join(problemas)

    @admin.display(description='motivo')
    def motivo_corto(self, obj):
        return Truncator(obj.motivo).chars(60)

    @admin.action(
        description='Aprobar y aplicar (solo las que pasan el verificador)',
        permissions=['aprobar'],
    )
    def aprobar_y_aplicar(self, request, queryset):
        aplicadas = 0
        ya_decididas = 0
        with transaction.atomic():
            for s in queryset.select_related('producto'):
                if s.estado != Sugerencia.PENDIENTE:
                    ya_decididas += 1
                    continue
                problemas = verificar(s)
                if problemas:
                    self.message_user(
                        request,
                        f'No se aplicó {s}: {"; ".join(problemas)}.',
                        messages.WARNING,
                    )
                    continue
                producto = s.producto
                anterior = getattr(producto, s.campo)
                nuevo = int(s.valor_propuesto) if s.campo == 'stock' else s.valor_propuesto
                s.valor_anterior = anterior
                setattr(producto, s.campo, nuevo)
                producto.save(update_fields=[s.campo])
                s.estado = Sugerencia.APROBADA
                s.decidido_por = request.user
                s.decidido_en = timezone.now()
                s.save()
                detalle = f'{s.campo}: {anterior} → {nuevo} (sugerencia #{s.pk})'
                self.log_change(request, producto, detalle)
                self.log_change(request, s, f'Aprobada y aplicada ({detalle})')
                aplicadas += 1
        if ya_decididas:
            self.message_user(
                request,
                f'{ya_decididas} sugerencia(s) ya estaba(n) decidida(s).',
                messages.INFO,
            )
        self.message_user(request, f'{aplicadas} sugerencia(s) aplicada(s).', messages.SUCCESS)

    @admin.action(description='Rechazar', permissions=['change'])
    def rechazar(self, request, queryset):
        n = 0
        for s in queryset.filter(estado=Sugerencia.PENDIENTE):
            s.estado = Sugerencia.RECHAZADA
            s.decidido_por = request.user
            s.decidido_en = timezone.now()
            s.save()
            self.log_change(request, s, 'Rechazada')
            n += 1
        self.message_user(request, f'{n} sugerencia(s) rechazada(s).', messages.SUCCESS)
