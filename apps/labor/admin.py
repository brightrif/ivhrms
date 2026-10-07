from django.contrib import admin

from .models import Contractor, LaborProfile, LaborRate, Trade

for m in (Trade, Contractor, LaborProfile, LaborRate):
    admin.site.register(m)
