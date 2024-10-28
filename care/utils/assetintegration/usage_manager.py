import json

from django.core.cache import cache

from care.users.models import User


class UsageManager:
    def __init__(self, asset_id: str, user: User):
        self.redis_client = cache.client.get_client()
        self.asset = str(asset_id)
        self.user = user
        self.waiting_list_cache_key = f"onvif_waiting_list:{asset_id}"
        self.current_user_cache_key = f"onvif_current_user:{asset_id}"

    def get_waiting_list(self) -> list[User]:
        asset_queue = self.redis_client.lrange(self.waiting_list_cache_key, 0, -1)
        return list(User.objects.filter(id__in=asset_queue))

    def add_to_waiting_list(self) -> int:
        if self.user.id not in self.redis_client.lrange(
            self.waiting_list_cache_key, 0, -1
        ):
            self.redis_client.rpush(self.waiting_list_cache_key, self.user.id)

        return self.redis_client.llen(self.waiting_list_cache_key)

    def remove_from_waiting_list(self) -> None:
        self.redis_client.lrem(self.waiting_list_cache_key, 0, self.user.id)

    def clear_waiting_list(self) -> None:
        self.redis_client.delete(self.waiting_list_cache_key)

    def current_user(self) -> dict:
        from care.facility.api.serializers.asset import UserBaseMinimumSerializer

        current_user = cache.get(self.current_user_cache_key)

        if current_user is None:
            return None

        user = User.objects.filter(id=current_user).first()

        if user is None:
            cache.delete(self.current_user_cache_key)
            return None

        return UserBaseMinimumSerializer(user).data

    def has_access(self) -> bool:
        current_user = cache.get(self.current_user_cache_key)
        return current_user is None or current_user == self.user.id

    def notify_waiting_list_on_asset_availabe(self) -> None:
        from care.utils.notification_handler import send_webpush

        message = json.dumps(
            {
                "type": "MESSAGE",
                "asset_id": self.asset,
                "message": "Camera is now available",
                "action": "CAMERA_AVAILABILITY",
            }
        )

        for user in self.get_waiting_list():
            send_webpush(username=user.username, message=message)

    def notify_current_user_on_request_access(self) -> None:
        from care.utils.notification_handler import send_webpush

        current_user = cache.get(self.current_user_cache_key)

        if current_user is None:
            return

        requester = User.objects.filter(id=self.user.id).first()

        if requester is None:
            return

        message = json.dumps(
            {
                "type": "MESSAGE",
                "asset_id": self.asset,
                "message": f"{User.REVERSE_TYPE_MAP[requester.user_type]}, {requester.full_name} ({requester.username}) has requested access to the camera",
                "action": "CAMERA_ACCESS_REQUEST",
            }
        )

        user = User.objects.filter(id=current_user).first()
        send_webpush(username=user.username, message=message)

    def lock_camera(self) -> bool:
        current_user = cache.get(self.current_user_cache_key)

        if current_user is None or current_user == self.user.id:
            cache.set(self.current_user_cache_key, self.user.id, timeout=60 * 5)
            self.remove_from_waiting_list()
            return True

        self.add_to_waiting_list()
        return False

    def unlock_camera(self) -> None:
        current_user = cache.get(self.current_user_cache_key)

        if current_user == self.user.id:
            cache.delete(self.current_user_cache_key)
            self.notify_waiting_list_on_asset_availabe()

        self.remove_from_waiting_list()

    def request_access(self) -> bool:
        if self.lock_camera():
            return True

        self.notify_current_user_on_request_access()
        return False

    def take_control(self):
        pass
