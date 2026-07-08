#!/usr/bin/env python3
from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node, SetParameter
from launch_ros.descriptions import ParameterFile
from nav2_common.launch import RewrittenYaml


def generate_launch_description():
    namespace = LaunchConfiguration("namespace")
    use_sim_time = LaunchConfiguration("use_sim_time")
    autostart = LaunchConfiguration("autostart")
    params_file = LaunchConfiguration("params_file")
    use_respawn = LaunchConfiguration("use_respawn")
    log_level = LaunchConfiguration("log_level")
    localization_backend = LaunchConfiguration("localization_backend")
    map_yaml = LaunchConfiguration("map")
    bt_xml = LaunchConfiguration("bt_xml")
    default_bt_xml = str(Path(__file__).resolve().parent / "behavior_trees" / "nav2_room_safe.xml")

    lifecycle_nodes = [
        "controller_server",
        "smoother_server",
        "planner_server",
        "behavior_server",
        "velocity_smoother",
        "bt_navigator",
        "waypoint_follower",
    ]
    localization_lifecycle_nodes = ["map_server", "amcl"]
    use_amcl = IfCondition(PythonExpression(["'", localization_backend, "' == 'amcl'"]))

    remappings = [("/tf", "tf"), ("/tf_static", "tf_static")]
    configured_params = ParameterFile(
        RewrittenYaml(
            source_file=params_file,
            root_key=namespace,
            param_rewrites={
                "autostart": autostart,
                "yaml_filename": map_yaml,
                "default_nav_to_pose_bt_xml": bt_xml,
            },
            convert_types=True,
        ),
        allow_substs=True,
    )

    return LaunchDescription(
        [
            SetEnvironmentVariable("RCUTILS_LOGGING_BUFFERED_STREAM", "1"),
            DeclareLaunchArgument("namespace", default_value="", description="Top-level namespace"),
            DeclareLaunchArgument("use_sim_time", default_value="false", description="Use simulation clock"),
            DeclareLaunchArgument("params_file", description="Full path to Nav2 params file"),
            DeclareLaunchArgument("map", default_value="", description="Full path to occupancy map yaml"),
            DeclareLaunchArgument("bt_xml", default_value=default_bt_xml, description="Nav2 behavior tree XML path"),
            DeclareLaunchArgument(
                "localization_backend",
                default_value="slam",
                description="Localization backend outside navigation: slam or amcl",
            ),
            DeclareLaunchArgument("autostart", default_value="true", description="Autostart lifecycle nodes"),
            DeclareLaunchArgument("use_respawn", default_value="False", description="Respawn Nav2 nodes"),
            DeclareLaunchArgument("log_level", default_value="info", description="Log level"),
            GroupAction(
                actions=[
                    SetParameter("use_sim_time", use_sim_time),
                    Node(
                        package="nav2_map_server",
                        executable="map_server",
                        name="map_server",
                        output="screen",
                        respawn=use_respawn,
                        respawn_delay=2.0,
                        parameters=[configured_params, {"yaml_filename": map_yaml}],
                        arguments=["--ros-args", "--log-level", log_level],
                        remappings=remappings,
                        condition=use_amcl,
                    ),
                    Node(
                        package="nav2_amcl",
                        executable="amcl",
                        name="amcl",
                        output="screen",
                        respawn=use_respawn,
                        respawn_delay=2.0,
                        parameters=[configured_params],
                        arguments=["--ros-args", "--log-level", log_level],
                        remappings=remappings,
                        condition=use_amcl,
                    ),
                    Node(
                        package="nav2_lifecycle_manager",
                        executable="lifecycle_manager",
                        name="lifecycle_manager_localization",
                        output="screen",
                        arguments=["--ros-args", "--log-level", log_level],
                        parameters=[{"autostart": autostart}, {"node_names": localization_lifecycle_nodes}],
                        condition=use_amcl,
                    ),
                    Node(
                        package="nav2_controller",
                        executable="controller_server",
                        output="screen",
                        respawn=use_respawn,
                        respawn_delay=2.0,
                        parameters=[configured_params],
                        arguments=["--ros-args", "--log-level", log_level],
                        remappings=remappings + [("cmd_vel", "cmd_vel_nav")],
                    ),
                    Node(
                        package="nav2_smoother",
                        executable="smoother_server",
                        name="smoother_server",
                        output="screen",
                        respawn=use_respawn,
                        respawn_delay=2.0,
                        parameters=[configured_params],
                        arguments=["--ros-args", "--log-level", log_level],
                        remappings=remappings,
                    ),
                    Node(
                        package="nav2_planner",
                        executable="planner_server",
                        name="planner_server",
                        output="screen",
                        respawn=use_respawn,
                        respawn_delay=2.0,
                        parameters=[configured_params],
                        arguments=["--ros-args", "--log-level", log_level],
                        remappings=remappings,
                    ),
                    Node(
                        package="nav2_behaviors",
                        executable="behavior_server",
                        name="behavior_server",
                        output="screen",
                        respawn=use_respawn,
                        respawn_delay=2.0,
                        parameters=[configured_params],
                        arguments=["--ros-args", "--log-level", log_level],
                        remappings=remappings + [("cmd_vel", "cmd_vel_nav")],
                    ),
                    Node(
                        package="nav2_bt_navigator",
                        executable="bt_navigator",
                        name="bt_navigator",
                        output="screen",
                        respawn=use_respawn,
                        respawn_delay=2.0,
                        parameters=[configured_params],
                        arguments=["--ros-args", "--log-level", log_level],
                        remappings=remappings,
                    ),
                    Node(
                        package="nav2_waypoint_follower",
                        executable="waypoint_follower",
                        name="waypoint_follower",
                        output="screen",
                        respawn=use_respawn,
                        respawn_delay=2.0,
                        parameters=[configured_params],
                        arguments=["--ros-args", "--log-level", log_level],
                        remappings=remappings,
                    ),
                    Node(
                        package="nav2_velocity_smoother",
                        executable="velocity_smoother",
                        name="velocity_smoother",
                        output="screen",
                        respawn=use_respawn,
                        respawn_delay=2.0,
                        parameters=[configured_params],
                        arguments=["--ros-args", "--log-level", log_level],
                        remappings=remappings
                        + [("cmd_vel", "cmd_vel_nav"), ("cmd_vel_smoothed", "cmd_vel")],
                    ),
                    Node(
                        package="nav2_lifecycle_manager",
                        executable="lifecycle_manager",
                        name="lifecycle_manager_navigation",
                        output="screen",
                        arguments=["--ros-args", "--log-level", log_level],
                        parameters=[{"autostart": autostart}, {"node_names": lifecycle_nodes}],
                    ),
                ],
            ),
        ]
    )
