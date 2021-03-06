# -*- coding: utf-8 -*-
#
# Copyright © 2012 - 2020 Michal Čihař <michal@cihar.com>
#
# This file is part of Weblate <https://weblate.org/>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#


from django.core.files import File
from django.urls import reverse
from rest_framework.test import APITestCase

from weblate.auth.models import Group, User
from weblate.screenshots.models import Screenshot
from weblate.trans.models import Change, Component, Project, Translation, Unit
from weblate.trans.tests.utils import RepoTestMixin, get_test_file
from weblate.utils.django_hacks import immediate_on_commit, immediate_on_commit_leave
from weblate.utils.state import STATE_TRANSLATED

TEST_PO = get_test_file('cs.po')
TEST_BADPLURALS = get_test_file('cs-badplurals.po')
TEST_SCREENSHOT = get_test_file('screenshot.png')


class APIBaseTest(APITestCase, RepoTestMixin):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        immediate_on_commit(cls)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        immediate_on_commit_leave(cls)

    def setUp(self):
        self.clone_test_repos()
        self.component = self.create_component()
        self.translation_kwargs = {
            'language__code': 'cs',
            'component__slug': 'test',
            'component__project__slug': 'test',
        }
        self.component_kwargs = {'slug': 'test', 'project__slug': 'test'}
        self.project_kwargs = {'slug': 'test'}
        self.tearDown()
        self.user = User.objects.create_user('apitest', 'apitest@example.org', 'x')
        group = Group.objects.get(name='Users')
        self.user.groups.add(group)

    def create_acl(self):
        project = Project.objects.create(
            name='ACL', slug='acl', access_control=Project.ACCESS_PRIVATE
        )
        self._create_component(
            'po-mono', 'po-mono/*.po', 'po-mono/en.po', project=project
        )

    def authenticate(self, superuser=False):
        if self.user.is_superuser != superuser:
            self.user.is_superuser = superuser
            self.user.save()
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + self.user.auth_token.key)

    def do_request(
        self,
        name,
        kwargs=None,
        data=None,
        code=200,
        superuser=False,
        method="get",
        request=None,
        skip=(),
    ):
        self.authenticate(superuser)
        url = reverse(name, kwargs=kwargs)
        response = getattr(self.client, method)(url, request)
        self.assertEqual(response.status_code, code)
        if data is not None:
            for item in skip:
                del response.data[item]
            self.assertEqual(response.data, data)
        return response


class ProjectAPITest(APIBaseTest):
    def test_list_projects(self):
        response = self.client.get(reverse('api:project-list'))
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['slug'], 'test')

    def test_list_projects_acl(self):
        self.create_acl()
        response = self.client.get(reverse('api:project-list'))
        self.assertEqual(response.data['count'], 1)
        self.authenticate(True)
        response = self.client.get(reverse('api:project-list'))
        self.assertEqual(response.data['count'], 2)

    def test_get_project(self):
        response = self.client.get(
            reverse('api:project-detail', kwargs=self.project_kwargs)
        )
        self.assertEqual(response.data['slug'], 'test')

    def test_repo_op_denied(self):
        for operation in ('push', 'pull', 'reset', 'cleanup', 'commit'):
            self.do_request(
                'api:project-repository',
                self.project_kwargs,
                code=403,
                method="post",
                request={'operation': operation},
            )

    def test_repo_ops(self):
        for operation in ('push', 'pull', 'reset', 'cleanup', 'commit'):
            self.do_request(
                'api:project-repository',
                self.project_kwargs,
                method="post",
                superuser=True,
                request={'operation': operation},
            )

    def test_repo_invalid(self):
        self.do_request(
            'api:project-repository',
            self.project_kwargs,
            code=400,
            method="post",
            superuser=True,
            request={'operation': 'invalid'},
        )

    def test_repo_status_denied(self):
        self.do_request('api:project-repository', self.project_kwargs, code=403)

    def test_repo_status(self):
        self.do_request(
            'api:project-repository',
            self.project_kwargs,
            superuser=True,
            data={'needs_push': False, 'needs_merge': False, 'needs_commit': False},
            skip=('url',),
        )

    def test_components(self):
        request = self.do_request('api:project-components', self.project_kwargs)
        self.assertEqual(request.data['count'], 1)

    def test_changes(self):
        request = self.do_request('api:project-changes', self.project_kwargs)
        self.assertEqual(request.data['count'], 12)

    def test_statistics(self):
        request = self.do_request('api:project-statistics', self.project_kwargs)
        self.assertEqual(request.data['total'], 16)

    def test_languages(self):
        request = self.do_request('api:project-languages', self.project_kwargs)
        self.assertEqual(len(request.data), 4)

    def test_delete(self):
        self.do_request(
            'api:project-detail', self.project_kwargs, method="delete", code=403
        )
        self.do_request(
            'api:project-detail',
            self.project_kwargs,
            method="delete",
            superuser=True,
            code=204,
        )
        self.assertEqual(Project.objects.count(), 0)

    def test_create(self):
        self.do_request(
            'api:project-list',
            method="post",
            code=403,
            request={
                'name': 'API project',
                'slug': 'api-project',
                'web': 'https://weblate.org/',
            },
        )
        self.do_request(
            'api:project-list',
            method="post",
            code=201,
            superuser=True,
            request={
                'name': 'API project',
                'slug': 'api-project',
                'web': 'https://weblate.org/',
            },
        )
        self.assertEqual(Project.objects.count(), 2)

    def test_create_component(self):
        self.do_request(
            'api:project-components',
            self.project_kwargs,
            method="post",
            code=403,
            request={
                'name': 'API project',
                'slug': 'api-project',
                'web': 'https://weblate.org/',
            },
        )
        response = self.do_request(
            'api:project-components',
            self.project_kwargs,
            method="post",
            code=201,
            superuser=True,
            request={
                'name': 'API project',
                'slug': 'api-project',
                'repo': self.format_local_path(self.git_repo_path),
                'filemask': 'po/*.po',
                'file_format': 'po',
                'push': 'https://username:password@github.com/example/push.git',
            },
        )
        self.assertEqual(Component.objects.count(), 2)
        self.assertEqual(
            Component.objects.get(slug='api-project', project__slug='test').push,
            'https://username:password@github.com/example/push.git',
        )
        self.assertEqual(response.data['push'], 'https://github.com/example/push.git')


class ComponentAPITest(APIBaseTest):
    def test_list_components(self):
        response = self.client.get(reverse('api:component-list'))
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['slug'], 'test')
        self.assertEqual(response.data['results'][0]['project']['slug'], 'test')

    def test_list_components_acl(self):
        self.create_acl()
        response = self.client.get(reverse('api:component-list'))
        self.assertEqual(response.data['count'], 1)
        self.authenticate(True)
        response = self.client.get(reverse('api:component-list'))
        self.assertEqual(response.data['count'], 2)

    def test_get_component(self):
        response = self.client.get(
            reverse('api:component-detail', kwargs=self.component_kwargs)
        )
        self.assertEqual(response.data['slug'], 'test')
        self.assertEqual(response.data['project']['slug'], 'test')

    def test_get_lock(self):
        response = self.client.get(
            reverse('api:component-lock', kwargs=self.component_kwargs)
        )
        self.assertEqual(response.data, {'locked': False})

    def test_set_lock_denied(self):
        self.authenticate()
        url = reverse('api:component-lock', kwargs=self.component_kwargs)
        response = self.client.post(url, {'lock': True})
        self.assertEqual(response.status_code, 403)

    def test_set_lock(self):
        self.authenticate(True)
        url = reverse('api:component-lock', kwargs=self.component_kwargs)
        response = self.client.get(url)
        self.assertEqual(response.data, {'locked': False})
        response = self.client.post(url, {'lock': True})
        self.assertEqual(response.data, {'locked': True})
        response = self.client.post(url, {'lock': False})
        self.assertEqual(response.data, {'locked': False})

    def test_repo_status_denied(self):
        self.do_request('api:component-repository', self.component_kwargs, code=403)

    def test_repo_status(self):
        self.do_request(
            'api:component-repository',
            self.component_kwargs,
            superuser=True,
            data={
                'needs_push': False,
                'needs_merge': False,
                'needs_commit': False,
                'merge_failure': None,
            },
            skip=('remote_commit', 'status', 'url'),
        )

    def test_statistics(self):
        self.do_request(
            'api:component-statistics',
            self.component_kwargs,
            data={'count': 4},
            skip=('results', 'previous', 'next'),
        )

    def test_new_template_404(self):
        self.do_request('api:component-new-template', self.component_kwargs, code=404)

    def test_new_template(self):
        self.component.new_base = 'po/cs.po'
        self.component.save()
        self.do_request('api:component-new-template', self.component_kwargs)

    def test_monolingual_404(self):
        self.do_request(
            'api:component-monolingual-base', self.component_kwargs, code=404
        )

    def test_monolingual(self):
        self.component.file_format = 'po-mono'
        self.component.filemask = 'po-mono/*.po'
        self.component.template = 'po-mono/en.po'
        self.component.save()
        self.do_request('api:component-monolingual-base', self.component_kwargs)

    def test_translations(self):
        request = self.do_request('api:component-translations', self.component_kwargs)
        self.assertEqual(request.data['count'], 4)

    def test_changes(self):
        request = self.do_request('api:component-changes', self.component_kwargs)
        self.assertEqual(request.data['count'], 12)

    def test_delete(self):
        self.do_request(
            'api:component-detail', self.component_kwargs, method="delete", code=403
        )
        self.do_request(
            'api:component-detail',
            self.component_kwargs,
            method="delete",
            superuser=True,
            code=204,
        )
        self.assertEqual(Component.objects.count(), 0)

    def test_create_translation(self):
        self.do_request(
            'api:component-translations',
            self.component_kwargs,
            method="post",
            code=201,
            request={'language_code': 'cs'},
        )

    def test_create_translation_invalid_language_code(self):
        self.do_request(
            'api:component-translations',
            self.component_kwargs,
            method="post",
            code=404,
            request={'language_code': 'invalid'},
        )


class LanguageAPITest(APIBaseTest):
    def test_list_languages(self):
        response = self.client.get(reverse('api:language-list'))
        self.assertEqual(response.data['count'], 4)

    def test_get_language(self):
        response = self.client.get(
            reverse('api:language-detail', kwargs={'code': 'cs'})
        )
        self.assertEqual(response.data['name'], 'Czech')


class TranslationAPITest(APIBaseTest):
    def test_list_translations(self):
        response = self.client.get(reverse('api:translation-list'))
        self.assertEqual(response.data['count'], 4)

    def test_list_translations_acl(self):
        self.create_acl()
        response = self.client.get(reverse('api:translation-list'))
        self.assertEqual(response.data['count'], 4)
        self.authenticate(True)
        response = self.client.get(reverse('api:translation-list'))
        self.assertEqual(response.data['count'], 8)

    def test_get_translation(self):
        response = self.client.get(
            reverse('api:translation-detail', kwargs=self.translation_kwargs)
        )
        self.assertEqual(response.data['language_code'], 'cs')

    def test_download(self):
        response = self.client.get(
            reverse('api:translation-file', kwargs=self.translation_kwargs)
        )
        self.assertContains(response, 'Project-Id-Version: Weblate Hello World 2016')

    def test_download_invalid_format(self):
        args = {'format': 'invalid'}
        args.update(self.translation_kwargs)
        response = self.client.get(reverse('api:translation-file', kwargs=args))
        self.assertEqual(response.status_code, 404)

    def test_download_format(self):
        args = {'format': 'xliff'}
        args.update(self.translation_kwargs)
        response = self.client.get(reverse('api:translation-file', kwargs=args))
        self.assertContains(response, '<xliff')

    def test_upload_denied(self):
        self.authenticate()
        # Remove all permissions
        self.user.groups.clear()
        with open(TEST_PO, 'rb') as handle:
            response = self.client.put(
                reverse('api:translation-file', kwargs=self.translation_kwargs),
                {'file': handle},
            )
        self.assertEqual(response.status_code, 404)

    def test_upload(self):
        self.authenticate()
        with open(TEST_PO, 'rb') as handle:
            response = self.client.put(
                reverse('api:translation-file', kwargs=self.translation_kwargs),
                {'file': handle},
            )
        self.assertEqual(
            response.data,
            {
                'accepted': 1,
                'count': 4,
                'not_found': 0,
                'result': True,
                'skipped': 0,
                'total': 4,
            },
        )
        translation = self.component.translation_set.get(language_code='cs')
        unit = translation.unit_set.get(source='Hello, world!\n')
        self.assertEqual(unit.target, 'Ahoj světe!\n')
        self.assertEqual(unit.state, STATE_TRANSLATED)

        self.assertEqual(self.component.project.stats.suggestions, 0)

    def test_upload_content(self):
        self.authenticate()
        with open(TEST_PO, 'rb') as handle:
            response = self.client.put(
                reverse('api:translation-file', kwargs=self.translation_kwargs),
                {'file': handle.read()},
            )
        self.assertEqual(response.status_code, 400)

    def test_upload_overwrite(self):
        self.test_upload()
        with open(TEST_PO, 'rb') as handle:
            response = self.client.put(
                reverse('api:translation-file', kwargs=self.translation_kwargs),
                {'file': handle, 'overwrite': 1},
            )
        self.assertEqual(
            response.data,
            {
                'accepted': 0,
                'count': 4,
                'not_found': 0,
                'result': False,
                'skipped': 1,
                'total': 4,
            },
        )

    def test_upload_suggest(self):
        self.authenticate()
        with open(TEST_PO, 'rb') as handle:
            response = self.client.put(
                reverse('api:translation-file', kwargs=self.translation_kwargs),
                {'file': handle, 'method': 'suggest'},
            )
        self.assertEqual(
            response.data,
            {
                'accepted': 1,
                'count': 4,
                'not_found': 0,
                'result': True,
                'skipped': 0,
                'total': 4,
            },
        )
        self.assertEqual(self.component.project.stats.suggestions, 1)
        with open(TEST_PO, 'rb') as handle:
            response = self.client.put(
                reverse('api:translation-file', kwargs=self.translation_kwargs),
                {'file': handle, 'method': 'suggest'},
            )
        self.assertEqual(
            response.data,
            {
                'accepted': 0,
                'count': 4,
                'not_found': 0,
                'result': False,
                'skipped': 1,
                'total': 4,
            },
        )

    def test_upload_invalid(self):
        self.authenticate()
        response = self.client.put(
            reverse('api:translation-file', kwargs=self.translation_kwargs)
        )
        self.assertEqual(response.status_code, 400)

    def test_upload_error(self):
        self.authenticate()
        with open(TEST_BADPLURALS, 'rb') as handle:
            response = self.client.put(
                reverse('api:translation-file', kwargs=self.translation_kwargs),
                {'file': handle},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('detail', response.data)

    def test_repo_status_denied(self):
        self.do_request('api:translation-repository', self.translation_kwargs, code=403)

    def test_repo_status(self):
        self.do_request(
            'api:translation-repository',
            self.translation_kwargs,
            superuser=True,
            data={
                'needs_push': False,
                'needs_merge': False,
                'needs_commit': False,
                'merge_failure': None,
            },
            skip=('remote_commit', 'status', 'url'),
        )

    def test_statistics(self):
        self.maxDiff = None
        self.do_request(
            'api:translation-statistics',
            self.translation_kwargs,
            data={
                'last_author': None,
                'code': 'cs',
                'failing_percent': 0.0,
                'url': 'http://example.com/engage/test/cs/',
                'translated_percent': 0.0,
                'total_words': 15,
                'failing': 0,
                'translated_words': 0,
                'url_translate': 'http://example.com/projects/test/test/cs/',
                'fuzzy_percent': 0.0,
                'translated': 0,
                'fuzzy': 0,
                'total': 4,
                'name': 'Czech',
                'recent_changes': 0,
            },
            skip=('last_change',),
        )

    def test_changes(self):
        request = self.do_request('api:translation-changes', self.translation_kwargs)
        self.assertEqual(request.data['count'], 2)

    def test_units(self):
        request = self.do_request('api:translation-units', self.translation_kwargs)
        self.assertEqual(request.data['count'], 4)

    def test_delete(self):
        self.assertEqual(Translation.objects.count(), 4)
        self.do_request(
            'api:translation-detail', self.translation_kwargs, method="delete", code=403
        )
        self.do_request(
            'api:translation-detail',
            self.translation_kwargs,
            method="delete",
            superuser=True,
            code=204,
        )
        self.assertEqual(Translation.objects.count(), 3)


class UnitAPITest(APIBaseTest):
    def test_list_units(self):
        response = self.client.get(reverse('api:unit-list'))
        self.assertEqual(response.data['count'], 16)

    def test_get_unit(self):
        unit = Unit.objects.filter(translation__language_code="cs")[0]
        response = self.client.get(reverse('api:unit-detail', kwargs={'pk': unit.pk}))
        self.assertIn('translation', response.data)


class ScreenshotAPITest(APIBaseTest):
    def setUp(self):
        super().setUp()
        shot = Screenshot.objects.create(name='Obrazek', component=self.component)
        with open(TEST_SCREENSHOT, 'rb') as handle:
            shot.image.save('screenshot.png', File(handle))

    def test_list_screenshots(self):
        response = self.client.get(reverse('api:screenshot-list'))
        self.assertEqual(response.data['count'], 1)

    def test_get_screenshot(self):
        response = self.client.get(
            reverse(
                'api:screenshot-detail', kwargs={'pk': Screenshot.objects.all()[0].pk}
            )
        )
        self.assertIn('file_url', response.data)

    def test_download(self):
        response = self.client.get(
            reverse(
                'api:screenshot-file', kwargs={'pk': Screenshot.objects.all()[0].pk}
            )
        )
        self.assertContains(response, b'PNG')

    def test_upload(self, superuser=True, code=200, filename=TEST_SCREENSHOT):
        self.authenticate(superuser)
        Screenshot.objects.all()[0].image.delete()

        self.assertEqual(Screenshot.objects.all()[0].image, '')
        with open(filename, 'rb') as handle:
            response = self.client.post(
                reverse(
                    'api:screenshot-file', kwargs={'pk': Screenshot.objects.all()[0].pk}
                ),
                {'image': handle},
            )
        self.assertEqual(response.status_code, code)
        if code == 200:
            self.assertTrue(response.data['result'])

            self.assertIn('.png', Screenshot.objects.all()[0].image.path)

    def test_upload_denied(self):
        self.test_upload(False, 403)

    def test_upload_invalid(self):
        self.test_upload(True, 400, TEST_PO)


class ChangeAPITest(APIBaseTest):
    def test_list_changes(self):
        response = self.client.get(reverse('api:change-list'))
        self.assertEqual(response.data['count'], 12)

    def test_get_change(self):
        response = self.client.get(
            reverse('api:change-detail', kwargs={'pk': Change.objects.all()[0].pk})
        )
        self.assertIn('translation', response.data)


class MetricsAPITest(APIBaseTest):
    def test_metrics(self):
        self.authenticate()
        response = self.client.get(reverse('api:metrics'))
        self.assertEqual(response.data['projects'], 1)

    def test_forbidden(self):
        response = self.client.get(reverse('api:metrics'))
        self.assertEqual(response.data['detail'].code, 'not_authenticated')
