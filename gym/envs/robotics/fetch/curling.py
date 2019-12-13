import os
import gym
from gym.envs.robotics import fetch_env
import numpy as np
from gym.envs.robotics import rotations, robot_env, utils

# Ensure we get the path separator correct on windows
MODEL_XML_PATH = os.path.join('fetch', 'curling.xml')

def goal_distance(goal_a, goal_b):
    assert goal_a.shape == goal_b.shape
    return np.linalg.norm(goal_a - goal_b, axis=-1)

class FetchCurlingEnv(robot_env.RobotEnv, gym.utils.EzPickle):
    def __init__(self, reward_type='sparse'):

        """Initializes a new Fetch environment.

        Args:
            model_path (string): path to the environments XML file
            n_substeps (int): number of substeps the simulation runs on every call to step
            gripper_extra_height (float): additional height above the table when positioning the gripper
            block_gripper (boolean): whether or not the gripper is blocked (i.e. not movable) or not
            has_object (boolean): whether or not the environment has an object
            target_in_the_air (boolean): whether or not the target should be in the air above the table or on the table surface
            target_offset (float or array with 3 elements): offset of the target
            obj_range (float): range of a uniform distribution for sampling initial object positions
            target_range (float): range of a uniform distribution for sampling a target
            distance_threshold (float): the threshold after which a goal is considered achieved
            initial_qpos (dict): a dictionary of joint names and values that define the initial configuration
            reward_type ('sparse' or 'dense'): the reward type, i.e. sparse or dense
        """
        initial_qpos = {
            'robot0:slide0': 0.05,
            'robot0:slide1': 0.48,
            'robot0:slide2': 0.0,
            'object0:joint': [1.7, 1.1, 0.41, 1., 0., 0., 0.],
        }
        model_path = MODEL_XML_PATH
        n_substeps = 20
        self.custom = True
        self.further = False
        self.gripper_extra_height = 0.2
        self.block_gripper = True
        self.has_object = True
        self.target_in_the_air = False
        self.target_offset = np.array([0.4, 0.0, 0.0])
        self.obj_range = 0.1
        self.target_range = 0.3
        self.init_range = 0.1
        self.distance_threshold = 0.1
        self.reward_type = reward_type
        # TODO: configure adaption parameters
        self.adapt_dict=dict()
        self.adapt_dict["mode"] = "standard"

        super(FetchCurlingEnv, self).__init__(
            model_path=model_path, n_substeps=n_substeps, n_actions=4,
            initial_qpos=initial_qpos)

        gym.utils.EzPickle.__init__(self)
    # GoalEnv methods
    # ----------------------------

    def compute_reward(self, achieved_goal, goal, info): # leave unchanged
        # Compute distance between goal and the achieved goal.
        d = goal_distance(achieved_goal, goal)
        if self.reward_type == 'sparse':
            return -(d > self.distance_threshold).astype(np.float32)
        else:
            return -d

    # RobotEnv methods
    # ----------------------------

    def _step_callback(self):
        # initially close gripper
        if self.block_gripper:
            self.sim.data.set_joint_qpos('robot0:l_gripper_finger_joint', 0.)
            self.sim.data.set_joint_qpos('robot0:r_gripper_finger_joint', 0.)
            self.sim.forward()

    def _set_action(self, action):
        assert action.shape == (4,)
        action = action.copy()  # ensure that we don't change the action outside of this scope
        pos_ctrl, gripper_ctrl = action[:3], action[3]

        pos_ctrl *= 0.05  # limit maximum change in position
        rot_ctrl = [1., 0., 1., 0.]  # fixed rotation of the end effector, expressed as a quaternion
        gripper_ctrl = np.array([gripper_ctrl, gripper_ctrl])
        assert gripper_ctrl.shape == (2,)
        if self.block_gripper:
            gripper_ctrl = np.zeros_like(gripper_ctrl)
        action = np.concatenate([pos_ctrl, rot_ctrl, gripper_ctrl])

        # Apply action to simulation.
        utils.ctrl_set_action(self.sim, action)
        utils.mocap_set_action(self.sim, action)

    def _get_obs(self):
        # positions
        grip_pos = self.sim.data.get_site_xpos('robot0:grip')
        dt = self.sim.nsubsteps * self.sim.model.opt.timestep
        grip_velp = self.sim.data.get_site_xvelp('robot0:grip') * dt
        robot_qpos, robot_qvel = utils.robot_get_obs(self.sim)
        if self.has_object:
            object_pos = self.sim.data.get_site_xpos('object0')
            # rotations
            object_rot = rotations.mat2euler(self.sim.data.get_site_xmat('object0'))
            # velocities
            object_velp = self.sim.data.get_site_xvelp('object0') * dt
            object_velr = self.sim.data.get_site_xvelr('object0') * dt
            # gripper state
            object_rel_pos = object_pos - grip_pos
            object_velp -= grip_velp
        else:
            object_pos = object_rot = object_velp = object_velr = object_rel_pos = np.zeros(0)
        gripper_state = robot_qpos[-2:]
        gripper_vel = robot_qvel[-2:] * dt  # change to a scalar if the gripper is made symmetric

        if not self.has_object:
            achieved_goal = grip_pos.copy()
        else:
            achieved_goal = np.squeeze(object_pos.copy())
        obs = np.concatenate([
            grip_pos, object_pos.ravel(), object_rel_pos.ravel(), gripper_state, object_rot.ravel(),
            object_velp.ravel(), object_velr.ravel(), grip_velp, gripper_vel,
        ])

        return {
            'observation': obs.copy(),
            'achieved_goal': achieved_goal.copy(),
            'desired_goal': self.goal.copy(),
        }

    def _viewer_setup(self):
        body_id = self.sim.model.body_name2id('robot0:gripper_link')
        lookat = self.sim.data.body_xpos[body_id]
        for idx, value in enumerate(lookat):
            self.viewer.cam.lookat[idx] = value
        self.viewer.cam.distance = 2.5
        self.viewer.cam.azimuth = 132.
        self.viewer.cam.elevation = -14.

    def _render_callback(self):
        # Visualize target.
        sites_offset = (self.sim.data.site_xpos - self.sim.model.site_pos).copy()
        site_id = self.sim.model.site_name2id('target0')
        self.sim.model.site_pos[site_id] = self.goal - sites_offset[0]
        self.sim.forward()

    def _reset_sim(self):
        self.sim.set_state(self.initial_state)

        # Randomize start position of object.
        if self.has_object:
            object_xpos = self.initial_gripper_xpos[:2]
            while np.linalg.norm(object_xpos - self.initial_gripper_xpos[:2]) < self.init_range:
                object_xpos = self.initial_gripper_xpos[:2] + self.np_random.uniform(-self.obj_range, self.obj_range, size=2)
            object_qpos = self.sim.data.get_joint_qpos('object0:joint')
            assert object_qpos.shape == (7,)
            object_qpos[:2] = object_xpos
            self.sim.data.set_joint_qpos('object0:joint', object_qpos)

        self.sim.forward()
        return True

    def _sample_goal(self):
        diag = self.sim.data.get_site_xpos('mark1b').copy()[:3] - self.sim.data.get_site_xpos('mark0a').copy()[:3]
        dist_x = diag[0]/2
        dist_y = diag[1]/2
        if self.adapt_dict["mode"] == 'standard':
            decision = self.np_random.uniform(5, 6)
        elif self.adapt_dict["mode"] == 'uniform':
            decision = self.np_random.uniform(0, 6)
        index = int(np.floor(decision))
        print(self.adapt_dict["mode"])
        #print("Decision {} --> {}".format(decision, index))
        marks = ['mark0a', 'mark1a', 'mark2a', 'mark3a', 'mark4a', 'mark5a']
        goal = self.sim.data.get_site_xpos(marks[index]).copy()[:3] + 0.5*diag
        goal[0] += self.np_random.uniform(-dist_x, dist_x)
        goal[1] += self.np_random.uniform(-dist_y, dist_y)

        return goal.copy()

    def _is_success(self, achieved_goal, desired_goal):
        d = goal_distance(achieved_goal, desired_goal)
        return (d < self.distance_threshold).astype(np.float32)

    def _env_setup(self, initial_qpos):
        for name, value in initial_qpos.items():
            self.sim.data.set_joint_qpos(name, value)
        utils.reset_mocap_welds(self.sim)
        self.sim.forward()

        # Move end effector into position.
        gripper_target = np.array([-0.498, 0.005, -0.431 + self.gripper_extra_height]) + self.sim.data.get_site_xpos('robot0:grip')
        gripper_rotation = np.array([1., 0., 1., 0.])
        self.sim.data.set_mocap_pos('robot0:mocap', gripper_target)
        self.sim.data.set_mocap_quat('robot0:mocap', gripper_rotation)
        for _ in range(10):
            self.sim.step()

        # Extract information for sampling goals.
        self.initial_gripper_xpos = self.sim.data.get_site_xpos('robot0:grip').copy()
        #print("Initial_gripper_xpos: {}".format(self.initial_gripper_xpos))

        # initial markers
        object_xpos = self.initial_gripper_xpos
        object_xpos[2] = 0.4
        #print("initial:")
        #print(self.initial_gripper_xpos)
        #TODO: der index 2 ist nur zufällig gewählt, da muss man aufpassen!
        sites_offset = (self.sim.data.site_xpos - self.sim.model.site_pos).copy()[2]
        #print("sites_offset:")
        #print(sites_offset)

        site_id = self.sim.model.site_name2id('init_a')
        self.sim.model.site_pos[site_id] = object_xpos + [self.init_range, self.init_range, 0.0] - sites_offset
        site_id = self.sim.model.site_name2id('init_b')
        self.sim.model.site_pos[site_id] = object_xpos + [self.init_range, -self.init_range, 0.0] - sites_offset
        site_id = self.sim.model.site_name2id('init_c')
        self.sim.model.site_pos[site_id] = object_xpos + [-self.init_range, self.init_range, 0.0] - sites_offset
        site_id = self.sim.model.site_name2id('init_d')
        self.sim.model.site_pos[site_id] = object_xpos + [-self.init_range, -self.init_range, 0.0] - sites_offset


        self.sim.step()


        if self.has_object:
            self.height_offset = self.sim.data.get_site_xpos('object0')[2]

    def render(self, mode='human', width=500, height=500):
        return super(FetchCurlingEnv, self).render(mode, width, height)
