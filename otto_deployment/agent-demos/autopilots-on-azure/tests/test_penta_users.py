import unittest

from scripts.penta_users import PROTECTED_UPN, PROTECTED_USER_ID, snapshot, validate_owned_user


class PentaUsersTests(unittest.TestCase):
    def test_tomas_is_never_mutable_even_with_owned_state(self):
        user = {"id": PROTECTED_USER_ID, "userPrincipalName": PROTECTED_UPN}
        with self.assertRaisesRegex(ValueError, "protected"):
            validate_owned_user(user, "tomas", {"users": {"tomas": user}})

    def test_untracked_existing_account_is_not_adopted(self):
        with self.assertRaisesRegex(ValueError, "not owned"):
            validate_owned_user({"id": "someone", "userPrincipalName": "emma@tomasonline.net"}, "emma", {})

    def test_owned_account_requires_matching_principal(self):
        state = {"users": {"emma": {"id": "emma-id"}}}
        validate_owned_user({"id": "emma-id", "userPrincipalName": "emma@tomasonline.net"}, "emma", state)
        with self.assertRaisesRegex(ValueError, "differ"):
            validate_owned_user({"id": "emma-id", "userPrincipalName": "other@tomasonline.net"}, "emma", state)

    def test_protected_snapshot_ignores_license_order(self):
        user = {
            "id": PROTECTED_USER_ID, "userPrincipalName": PROTECTED_UPN,
            "displayName": "TomasOnlineNet", "accountEnabled": True,
            "assignedLicenses": [{"skuId": "b"}, {"skuId": "a"}],
        }
        before = snapshot(user)
        user["assignedLicenses"].reverse()
        self.assertEqual(before, snapshot(user))
        user["displayName"] = "Changed"
        self.assertNotEqual(before, snapshot(user))


if __name__ == "__main__":
    unittest.main()
