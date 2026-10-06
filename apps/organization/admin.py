from django.contrib import admin
from .models import Department, Designation, Grade, Location, Project, Company,CompanyAccess

for m in (Department, Designation, Grade, Location, Project, Company, CompanyAccess):
    admin.site.register(m)