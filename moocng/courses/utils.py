# -*- coding: utf-8 -*-
# Copyright 2012-2013 Rooter Analysis S.L.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import json
import os

from datetime import date
from deep_serializer import serializer, deserializer


from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMessage, EmailMultiAlternatives, get_connection
from django.template import loader

from moocng.badges.models import Badge
from moocng.courses.models import Course, Unit, KnowledgeQuantum, Question, Option, Attachment
from moocng.courses.serializer import (CourseClone, UnitClone, KnowledgeQuantumClone,
                                       BaseMetaWalkClass, QuestionClone, PeerReviewAssignmentClone,
                                       EvaluationCriterionClone, OptionClone, AttachmentClone)
from moocng import mongodb
from moocng.peerreview.models import PeerReviewAssignment, EvaluationCriterion

logger = logging.getLogger(__name__)


TRACE_CLONE_COURSE_DIR = 'trace_clone_course'


def is_teacher(user, courses):

    """
    Return if a user is teacher of a course or not

    :returns: Boolean

    .. versionadded:: 0.1
    """
    is_teacher = False
    if isinstance(courses, Course):
        courses = [courses]
    if user.is_authenticated():
        for course in courses:
            is_teacher = is_teacher or course.teachers.filter(id=user.id).exists()
    return is_teacher


UNIT_BADGE_CLASSES = {
    'n': 'badge-inverse',
    'h': 'badge-warning',
    'e': 'badge-important',
}


def get_unit_badge_class(unit):

    """
    .. versionadded:: 0.1
    """
    return UNIT_BADGE_CLASSES[unit.unittype]


def is_course_ready(course):

    """
    Return if the current course is ready for users. This is done by comparing
    the start and end dates of the course.

    :returns: Boolean pair

    .. versionadded:: 0.1
    """
    has_content = course.unit_set.count() > 0
    is_ready = True
    ask_admin = False
    if course.start_date:
        is_ready = date.today() >= course.start_date
        if is_ready and not has_content:
            is_ready = False
            ask_admin = True
    else:
        if not has_content:
            is_ready = False
            ask_admin = True
    return (is_ready, ask_admin)


def send_mail_wrapper(subject, template, context, to):

    """
    Simple wrapper on top of the django send_mail function.

    .. versionadded:: 0.1
    """
    try:
        email = EmailMessage(
            subject=subject,
            body=loader.render_to_string(template, context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=to
        )
        email.send()
    except IOError as ex:
        logger.error('The notification "%s" to %s could not be sent because of %s' % (subject, str(to), str(ex)))


def send_mass_mail_wrapper(subject, message, recipients, html_content=False):

    """
    Simple wrapper on top of the django send_mass_mail function.

    .. versionadded: 0.1
    """
    mails = []
    content = message
    if html_content:
        content = ""
    for to in recipients:
        email = EmailMultiAlternatives(subject, content, settings.DEFAULT_FROM_EMAIL, [to])
        if html_content:
            email.attach_alternative(message, "text/html")
        mails.append(email)
    try:
        get_connection().send_messages(mails)
    except IOError as ex:
        logger.error('The massive email "%s" to %s could not be sent because of %s' % (subject, recipients, str(ex)))


def get_trace_clone_file_name(original_course, copy_course):
    return '%s_original_pk_%s_copy_pk_%s.json' % (original_course.slug,
                                                  original_course.pk,
                                                  copy_course.pk)


def get_trace_clone_dir_path():
    return os.path.join(settings.MEDIA_ROOT, TRACE_CLONE_COURSE_DIR)


def get_trace_clone_file_path(file_name):
    return os.path.join(get_trace_clone_dir_path(), file_name)


def clone_course(course, request):
    """
    Returns a clone of the course param and its relations
    """
    walking_classes = {Course: CourseClone,
                       User: BaseMetaWalkClass,
                       Badge: BaseMetaWalkClass,
                       Unit: UnitClone,
                       KnowledgeQuantum: KnowledgeQuantumClone,
                       Attachment: AttachmentClone,
                       Question: QuestionClone,
                       Option: OptionClone,
                       PeerReviewAssignment: PeerReviewAssignmentClone,
                       EvaluationCriterion: EvaluationCriterionClone}
    fixtures_format = 'json'
    fixtures_json = serializer(fixtures_format,
                               initial_obj=course,
                               walking_classes=walking_classes,
                               natural_keys=True,
                               request=request)
    objs = deserializer(fixtures_format,
                        fixtures_json,
                        initial_obj=course,
                        walking_classes=walking_classes)
    course.slug = course.slug_original
    dir_path = get_trace_clone_dir_path()
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)
    file_name = get_trace_clone_file_name(course, objs[0])
    file_path = get_trace_clone_file_path(file_name)
    f = open(file_path, 'w')
    f.write(json.dumps(course.trace_ids, indent=4))
    if request:
        return objs, file_name
    return objs, file_path


def clone_activiy_user_course(original_course, copy_course, user):
    file_name = get_trace_clone_file_name(original_course, copy_course)
    file_path = get_trace_clone_file_path(file_name)
    f = open(file_path)
    trace_ids = json.loads(f.read())
    f.close()
    if not copy_course.pk == trace_ids['Course'][str(original_course.pk)]:
        raise ValueError

    mongo_db = mongodb.get_db()
    activity = mongo_db.get_collection('activity')
    original_act_docs = activity.find({"user_id": user.pk,
                                       "course_id": original_course.pk})
    new_act_docs = []
    for ori_act_doc in original_act_docs:
        try:
            ori_kq_id = str(int(ori_act_doc['kq_id']))
            ori_unit_id = str(int(ori_act_doc['unit_id']))
        except (ValueError, TypeError):
            continue
        new_act_doc = {}
        try:
            new_act_doc['user_id'] = user.pk
            new_act_doc['course_id'] = copy_course.pk
            new_act_doc['kq_id'] = trace_ids['KnowledgeQuantum'][ori_kq_id]
            new_act_doc['unit_id'] = trace_ids['Unit'][ori_unit_id]
        except KeyError:
            continue
        exists_doc = activity.find(new_act_doc).count() > 0
        if not exists_doc:
            new_act_docs.append(new_act_doc)
    if new_act_docs:
        activity.insert(new_act_docs)

    answers = mongo_db.get_collection('answers')
    original_answer_docs = answers.find({"user_id": user.pk,
                                         "course_id": original_course.pk})
    insert_answer_docs = []
    update_answer_docs = {}
    for answer_doc in original_answer_docs:
        try:
            ori_kq_id = str(int(answer_doc['kq_id']))
            ori_question_id = str(int(answer_doc['question_id']))
            ori_unit_id = str(int(answer_doc['unit_id']))
        except (ValueError, TypeError):
            continue
        new_answer_doc = {}
        try:
            new_answer_doc['user_id'] = user.pk
            new_answer_doc['course_id'] = copy_course.pk
            new_answer_doc['kq_id'] = trace_ids['KnowledgeQuantum'][ori_kq_id]
            new_answer_doc['question_id'] = trace_ids['Question'][ori_question_id]
            new_answer_doc['unit_id'] = trace_ids['Unit'][ori_unit_id]
        except KeyError:
            continue
        exists_doc_without_reply = answers.find_one(new_answer_doc)
        replyList = answer_doc['replyList']
        if not isinstance(replyList, list):
            continue
        for reply in replyList:
            try:
                reply['option'] = trace_ids['Option'][str(int(reply['option']))]
            except KeyError:
                continue
        new_answer_doc['replyList'] = answer_doc['replyList']
        exists_doc = answers.find_one(new_answer_doc)
        if not exists_doc_without_reply:
            new_answer_doc['date'] = answer_doc['date']
            insert_answer_docs.append(new_answer_doc)
        elif exists_doc_without_reply and not exists_doc:
            update_answer_docs[exists_doc_without_reply['_id']] = new_answer_doc
    if insert_answer_docs:
        answers.insert(insert_answer_docs)
    if update_answer_docs:
        for _id, update_answer_doc in update_answer_docs.items():
            answers.update({'_id': _id},
                           {'$set': {'replyList': update_answer_doc['replyList']}},
                           upsert=True)
    return (new_act_docs, insert_answer_docs, update_answer_docs)
