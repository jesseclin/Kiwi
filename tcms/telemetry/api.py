from django.db.models import Count
from django.utils.translation import ugettext_lazy as _
from modernrpc.core import rpc_method

from tcms.testcases.models import TestCase, TestCaseStatus
from tcms.testruns.models import TestExecution, TestExecutionStatus


@rpc_method(name='Testing.breakdown')
def breakdown(query=None):
    """
    .. function:: XML-RPC Testing.breakdown(query)

        Perform a search and return the statistics for the selected test cases

        :param query: Field lookups for :class:`tcms.testcases.models.TestCase`
        :type query: dict
        :return: Object, containing the statistics for the selected test cases
        :rtype: dict
    """

    if query is None:
        query = {}

    test_cases = TestCase.objects.filter(**query).filter()

    manual_count = test_cases.filter(is_automated=False).count()
    automated_count = test_cases.filter(is_automated=True).count()
    count = {
        'manual': manual_count,
        'automated': automated_count,
        'all': manual_count + automated_count
    }

    priorities = _get_field_count_map(test_cases, 'priority', 'priority__value')
    categories = _get_field_count_map(test_cases, 'category', 'category__name')

    return {
        'count': count,
        'priorities': priorities,
        'categories': categories,
    }


def _get_field_count_map(test_cases, expression, field):
    confirmed = TestCaseStatus.get_confirmed()

    query_set_confirmed = test_cases.filter(
        case_status=confirmed
    ).values(field).annotate(
        count=Count(expression)
    )
    query_set_not_confirmed = test_cases.exclude(
        case_status=confirmed
    ).values(field).annotate(
        count=Count(expression)
    )
    return {
        confirmed.name: _map_query_set(query_set_confirmed, field),
        str(_('OTHER')): _map_query_set(query_set_not_confirmed, field)
    }


def _map_query_set(query_set, field):
    return {entry[field]: entry['count'] for entry in query_set}


@rpc_method(name='Testing.status_matrix')
def status_matrix(query=None):
    """
        .. function:: XML-RPC Testing.status_matrix(query)

            Perform a search and return data_set needed to visualize the status matrix
            of test plans, test cases and test executions

            :param query: Field lookups for :class:`tcms.testcases.models.TestPlan`
            :type query: dict
            :return: List, containing the information about the test executions
            :rtype: list
        """
    if query is None:
        query = {}

    data_set = []
    columns = {}
    row = {'tc_id': 0}
    for test_execution in TestExecution.objects.filter(**query).only(
            'case_id', 'run_id', 'case__summary', 'status'
    ).order_by('case_id', 'run_id'):

        columns[test_execution.run.run_id] = test_execution.run.summary
        test_execution_response = {
            'pk': test_execution.pk,
            'class': test_execution.status.color_code(),
            'run_id': test_execution.run_id,
        }

        if test_execution.case_id == row['tc_id']:
            row['executions'].append(test_execution_response)
        else:
            data_set.append(row)

            row = {
                'tc_id': test_execution.case_id,
                'tc_summary': test_execution.case.summary,
                'executions': [test_execution_response],
            }

    # append the last row
    data_set.append(row)

    del data_set[0]

    return {
        'data': data_set,
        'columns': columns
    }


@rpc_method(name='Testing.execution_trends')
def execution_trends(query=None):

    if query is None:
        query = {}

    data_set = {}
    categories = []
    colors = []
    count = {}

    for status in TestExecutionStatus.objects.all():
        data_set[status.color_code()] = []

        color = status.color()
        if color not in colors:
            colors.append(color)

    run_id = 0
    for test_execution in TestExecution.objects.filter(**query).order_by('run_id'):
        status = test_execution.status.color_code()

        if test_execution.run_id == run_id:
            if status in count:
                count[status] += 1
            else:
                count[status] = 1

        else:
            _append_status_counts_to_result(count, data_set)

            count = {}
            run_id = test_execution.run_id
            categories.append(run_id)

    # append the last result
    _append_status_counts_to_result(count, data_set)

    for _key, value in data_set.items():
        del value[0]

    return {
        'categories': categories,
        'data_set': data_set,
        'colors': colors
    }


def _append_status_counts_to_result(count, result):
    for status in TestExecutionStatus.chart_status_names:
        status_count = count.get(status, 0)
        result.get(status).append(status_count)


@rpc_method(name='Testing.test_case_health')
def test_case_health(query=None):

    if query is None:
        query = {}

    all_test_executions = TestExecution.objects.filter(**query)

    test_executions = _get_count_for(all_test_executions)
    passed_test_executions = _get_count_for(all_test_executions.filter(
        status__name__in=TestExecutionStatus.passed_status_names))
    failed_test_executions = _get_count_for(all_test_executions.filter(
        status__name__in=TestExecutionStatus.failure_status_names))

    data = {}
    for te in test_executions:
        data[te['case_id']] = {
            'case_id': te['case_id'],
            'case_summary': te['case__summary'],
            'count': {
                'all': te['count'],
                'success': 0,
                'fail': 0
            }
        }

    _count_test_executions(data, passed_test_executions, 'success')
    _count_test_executions(data, failed_test_executions, 'fail')

    # remove all with 100% success rate, because they are not interesting
    _remove_all_excellent_executions(data)

    data = list(data.values())

    data.sort(key=_sort_by_passing_rate)

    return _build_result_from_data(data)


def _remove_all_excellent_executions(data):
    for key in dict.fromkeys(data):
        if data[key]['count']['success'] == data[key]['count']['all']:
            data.pop(key)


def _build_result_from_data(data):
    result = []
    zero_success_rate_count = 0
    close_to_zero_rate_count = 0
    close_to_full_rate_count = 0

    for element in data:
        if len(result) > 30:
            break

        success_count = element['count']['success']
        if success_count == 0 and zero_success_rate_count < 10:
            result.append(element)
            zero_success_rate_count += 1
        elif success_count <= 0.5 and close_to_zero_rate_count < 10:
            result.append(element)
            close_to_zero_rate_count += 1
        elif success_count > 0.5 and close_to_full_rate_count < 10:
            result.append(element)
            close_to_full_rate_count += 1

    return result


def _count_test_executions(data, test_executions, status):

    for te in test_executions:
        data[te['case_id']]['count'][status] = te['count']


def _sort_by_passing_rate(element):
    return element['count']['success'] / element['count']['all']


def _get_count_for(test_executions):
    return test_executions.values(
        'case_id', 'case__summary'
    ).annotate(count=Count('case_id')).order_by('case_id')
