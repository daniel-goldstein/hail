import asyncio

from hailtop.auth import async_create_user


class CreateUserException(Exception):
    pass


def init_parser(parser):
    parser.add_argument("username", type=str,
                        help="User name to create.")
    parser.add_argument("login_id", type=str,
                        help="Login ID to be used with OAuth. This is the object ID in Azure and the email address in GCP.")
    parser.add_argument("--developer", default=False, action='store_true',
                        help="User should be a developer.")
    parser.add_argument("--service-account", default=False, action='store_true',
                        help="User should be a service account.")
    parser.add_argument("--namespace", "-n", type=str,
                        help="Specify namespace for auth server.  (default: from deploy configuration)")
    parser.add_argument("--wait", default=False, action='store_true',
                        help="Wait for the creation of the user to finish")


def main(args, pass_through_args):  # pylint: disable=unused-argument
    loop = asyncio.get_event_loop()
    loop.run_until_complete(async_create_user(args.username, args.login_id, args.developer, args.service_account, args.namespace, wait=args.wait))
